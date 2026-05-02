import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import admin_tools
from scripts import api_server
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_admin_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個後台工具測試都使用暫存 DB，避免影響本機資料。"""
    data_dir = tmp_path / "databases"

    monkeypatch.setattr(api_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(api_server, "ACCOUNTS_DB", data_dir / "accounts.db")
    monkeypatch.setattr(api_server, "BUY_ORDERS_DB", data_dir / "buy_orders.db")
    monkeypatch.setattr(api_server, "SELL_ORDERS_DB", data_dir / "sell_orders.db")

    monkeypatch.setattr(orchestrator_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(orchestrator_server, "BUY_ORDERS_DB", data_dir / "buy_orders.db")
    monkeypatch.setattr(orchestrator_server, "SELL_ORDERS_DB", data_dir / "sell_orders.db")
    monkeypatch.setattr(orchestrator_server, "ORCHESTRATOR_STATE_DB", data_dir / "orchestrator_state.db")
    monkeypatch.setattr(orchestrator_server, "TIMEOUT_ORDERS_DB", data_dir / "timeout_orders.db")
    monkeypatch.setattr(orchestrator_server, "DECISIONS_DB", data_dir / "decisions.db")
    monkeypatch.setattr(orchestrator_server, "EXECUTIONS_DB", data_dir / "executions.db")

    api_server.SESSIONS.clear()
    api_server.NONCES.clear()
    api_server._init_databases()
    orchestrator_server._ensure_databases()


def create_account(wallet_address: str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") -> str:
    """建立測試錢包帳號並回傳標準化地址。"""
    now = datetime.now(timezone.utc).isoformat()
    normalized = wallet_address.lower()
    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        conn.execute(
            """
            INSERT INTO accounts (wallet_address, account_level, day, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                normalized,
                "free",
                0,
                now,
            ),
        )
        conn.commit()
    return normalized


def insert_buy_order(account_name: str = "buyer") -> int:
    """插入買單測試資料。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(api_server.BUY_ORDERS_DB) as conn:
        cursor = conn.execute(
            """
            INSERT INTO buy_orders (
                account_name,
                account_level_snapshot,
                asset,
                amount,
                remaining_amount,
                max_unit_price_usdc,
                max_splits,
                max_fee_percent,
                status,
                attempts,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_name, "free", "WETH", 2, 2, 3000, 3, 0.3, "pending", 0, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order(account_name: str = "seller") -> int:
    """插入賣單測試資料。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(api_server.SELL_ORDERS_DB) as conn:
        cursor = conn.execute(
            """
            INSERT INTO sell_orders (
                account_name,
                account_level_snapshot,
                asset,
                amount,
                remaining_amount,
                min_unit_price_usdc,
                max_splits,
                max_fee_percent,
                status,
                attempts,
                created_at,
                updated_at,
                queue_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_name, "free", "WETH", 1, 1, 2900, 3, 0.3, "pending", 0, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_admin_can_update_account_level_and_day_without_exposing_password_hash() -> None:
    """測試後台可調整帳號等級與 day，查詢時不暴露密碼 hash/salt。"""
    wallet_address = create_account()

    level_result = admin_tools.set_account_level(wallet_address, "max")
    day_result = admin_tools.set_account_day(wallet_address, 12)
    account = admin_tools.get_account(wallet_address)

    assert level_result["accountLevel"] == "max"
    assert day_result["day"] == 12
    assert account == {
        "walletAddress": wallet_address,
        "accountLevel": "max",
        "day": 12,
        "createdAt": account["createdAt"],
    }
    assert "password_hash" not in account
    assert "salt" not in account


def test_admin_rejects_invalid_account_level_and_negative_day() -> None:
    """測試後台會拒絕不合法帳號等級與負數 day。"""
    wallet_address = create_account()

    with pytest.raises(ValueError, match="account level"):
        admin_tools.set_account_level(wallet_address, "pro")
    with pytest.raises(ValueError, match="day"):
        admin_tools.set_account_day(wallet_address, -1)


def test_admin_can_list_orders_and_snapshot() -> None:
    """測試後台可查詢訂單與訂單簿快照。"""
    buy_id = insert_buy_order("buyer")
    sell_id = insert_sell_order("seller")

    orders = admin_tools.list_orders(status="pending")
    snapshot = admin_tools.get_order_book_snapshot(limit_per_side=5)

    assert orders["buyOrders"][0]["id"] == buy_id
    assert orders["buyOrders"][0]["accountName"] == "buyer"
    assert orders["sellOrders"][0]["id"] == sell_id
    assert orders["sellOrders"][0]["accountName"] == "seller"
    assert snapshot["queueStatus"]["sellQueues"]["free"] == 1
    assert snapshot["buyStatusCounts"] == {"pending": 1}
    assert snapshot["sellStatusCounts"] == {"pending": 1}


def test_admin_can_read_decision_and_execution_records() -> None:
    """測試後台可查詢 decision 回放資料與 execution 提案資料。"""
    buy_id = insert_buy_order("buyer")
    sell_id = insert_sell_order("seller")

    task = orchestrator_server.prepare_agent_task(candidate_limit=3)["task"]
    proposed = orchestrator_server.apply_agent_decision(
        {
            "taskId": task["taskId"],
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1,
                    "unitPriceUsdc": 2950,
                }
            ],
            "executionPayload": {
                "intentA": {"intent": None, "signature": None},
                "actionType": 1,
                "executeAmountIn": "1",
                "routeDetails": {
                    "Calldata": None,
                    "matchedIntentB": {"intent": None, "signature": None, "executeAmountInB": "2950"},
                    "treasuryAmountOut": None,
                },
            },
        }
    )

    decision = admin_tools.get_decision(task["taskId"])
    execution = admin_tools.get_execution(proposed["executionId"])

    assert decision["taskId"] == task["taskId"]
    assert decision["candidateBuyOrderIds"] == [buy_id]
    assert decision["agentDecision"]["decisionStatus"] == "proposed_execution"
    assert execution["executionId"] == proposed["executionId"]
    assert execution["status"] == "proposed"
    assert execution["proposal"]["matches"][0]["buyOrderId"] == buy_id
