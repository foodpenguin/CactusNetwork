import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import api_server
from scripts import execution_messages
from scripts import execution_reconciler
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_reconciler_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個 execution reconciler 測試都使用暫存 DB。"""
    data_dir = tmp_path / "databases"
    for key in (
        "SP_TESTNET_RPC_URL",
        "SEPOLIA_RPC_URL",
        "RPC_URL",
        "INTENT_VAULT_ADDRESS",
        "SETTLEMENT_ROUTER_ADDRESS",
        "PROTOCOL_TREASURY_ADDRESS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ONCHAIN_PREFLIGHT_CHECKS", "disabled")

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

    api_server._init_databases()
    orchestrator_server._ensure_databases()


def insert_buy_order(amount: float = 2) -> int:
    """插入買單測試資料。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(orchestrator_server.BUY_ORDERS_DB) as conn:
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
            ("buyer", "free", "WETH", amount, amount, 3000, 3, 0.3, "pending", 0, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order(amount: float = 1) -> int:
    """插入賣單測試資料。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(orchestrator_server.SELL_ORDERS_DB) as conn:
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
            ("seller", "free", "WETH", amount, amount, 2900, 3, 0.3, "pending", 0, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def make_payload(deadline: int = 1999999999) -> dict:
    """建立可送 KeeperHub 的 OTC payload。"""
    return {
        "intentA": {
            "intent": {
                "user": "0xSeller",
                "tokenIn": "0xWETH",
                "tokenOut": "0xUSDC",
                "amountIn": "1",
                "minAmountOut": "2900",
                "deadline": deadline,
                "salt": "0xsell",
                "allowPartialFill": True,
            },
            "signature": "0xsell_signature",
        },
        "actionType": 1,
        "executeAmountIn": "1",
        "routeDetails": {
            "Calldata": None,
            "matchedIntentB": {
                "intent": {
                    "user": "0xBuyer",
                    "tokenIn": "0xUSDC",
                    "tokenOut": "0xWETH",
                    "amountIn": "2900",
                    "minAmountOut": "1",
                    "deadline": deadline,
                    "salt": "0xbuy",
                    "allowPartialFill": True,
                },
                "signature": "0xbuy_signature",
                "executeAmountInB": "2900",
            },
            "treasuryAmountOut": None,
        },
    }


def make_external_dex_payload(deadline: int = 1999999999) -> dict:
    """建立 actionType=0 的外部 DEX payload。"""
    payload = make_payload(deadline=deadline)
    payload["actionType"] = 0
    payload["routeDetails"]["Calldata"] = "0x04e45aaf"
    payload["routeDetails"]["matchedIntentB"] = None
    return payload


def create_execution(payload: dict) -> tuple[str, int, int]:
    """建立 proposed execution 測試資料。"""
    buy_id = insert_buy_order()
    sell_id = insert_sell_order()
    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [{"buyOrderId": buy_id, "filledAmount": 1, "unitPriceUsdc": 2900}],
            "executionPayload": payload,
        }
    )
    return result["executionId"], buy_id, sell_id


def fetch_execution_status(execution_id: str) -> str:
    """讀取 execution 狀態。"""
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        row = conn.execute("SELECT status FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
    assert row is not None
    return str(row[0])


def fetch_order_remaining(db_path: Path, table_name: str, order_id: int) -> tuple[float, str]:
    """讀取訂單剩餘量與狀態。"""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT remaining_amount, status FROM {table_name} WHERE id = ?",
            (order_id,),
        ).fetchone()
    assert row is not None
    return row


def test_reconcile_expires_incomplete_proposed_execution() -> None:
    """測試缺欄位的 proposed execution 會被標成 failed，避免永久鎖單。"""
    payload = make_payload()
    payload["intentA"]["signature"] = ""
    execution_id, buy_id, sell_id = create_execution(payload)

    result = execution_reconciler.reconcile_keeperhub_executions(limit=5)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["summary"]["expiredCount"] == 1
    assert result["expired"][0]["executionStatus"] == "failed"
    assert fetch_execution_status(execution_id) == "failed"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"


def test_reconcile_expires_deadline_passed_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 intent deadline 已過期時不送 KeeperHub，直接 failed 釋放鎖。"""
    payload = make_payload(deadline=1)
    execution_id, _, _ = create_execution(payload)

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        raise AssertionError("expired execution should not be posted")

    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_reconciler.reconcile_keeperhub_executions(limit=5)

    assert result["summary"]["expiredCount"] == 1
    assert result["summary"]["dispatchedCount"] == 0
    assert "deadline" in result["expired"][0]["confirmResult"]["failureReason"]
    assert fetch_execution_status(execution_id) == "failed"


def test_external_dex_payload_does_not_require_matched_intent_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 actionType=0 外部 DEX payload 不會檢查 matchedIntentB deadline。"""
    execution_id, _, _ = create_execution(make_external_dex_payload())
    captured: dict[str, object] = {}

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        captured["payload"] = posted_payload
        return {
            "httpStatusCode": 200,
            "body": {"status": "failed", "error": "mock keeperhub failed after dispatch"},
        }

    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_reconciler.reconcile_keeperhub_executions(limit=5)

    assert result["summary"]["expiredCount"] == 0
    assert result["summary"]["dispatchedCount"] == 1
    assert captured["payload"]["actionType"] == 0
    assert captured["payload"]["routeDetails"]["matchedIntentB"] is None
    assert fetch_execution_status(execution_id) == "failed"


def test_reconcile_dispatches_ready_execution_and_applies_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 ready execution 會自動送 KeeperHub，confirmed 後正式更新買賣單。"""
    execution_id, buy_id, sell_id = create_execution(make_payload())
    captured: dict[str, object] = {}

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        captured["payload"] = posted_payload
        return {
            "httpStatusCode": 200,
            "body": {"status": "confirmed", "tx_hash": "0xauto", "block_number": 123},
        }

    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_reconciler.reconcile_keeperhub_executions(limit=5)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["summary"]["dispatchedCount"] == 1
    assert result["dispatched"][0]["executionStatus"] == "confirmed"
    assert captured["payload"]["intentA"]["signature"] == "0xsell_signature"
    assert fetch_execution_status(execution_id) == "confirmed"
    assert buy_remaining == 1
    assert buy_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"


def test_reconcile_expires_onchain_preflight_failed_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試鏈上預檢失敗時不送 KeeperHub，直接 failed 並釋放買賣單鎖定。"""
    execution_id, buy_id, sell_id = create_execution(make_payload())

    def fake_preflight(payload: dict) -> dict:
        return {
            "status": "failed",
            "ready": False,
            "failureReason": "intentA vaultBalance=0 < executeAmountIn=1",
            "checks": [
                {
                    "label": "intentA",
                    "vaultBalance": "0",
                    "executeAmountIn": "1",
                    "hasEnoughVaultBalance": False,
                    "hasEnoughRemainingAmount": True,
                    "isExecutable": False,
                }
            ],
        }

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        raise AssertionError("preflight failed execution should not be posted")

    monkeypatch.setattr(execution_messages, "check_execution_payload_onchain_preflight", fake_preflight)
    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_reconciler.reconcile_keeperhub_executions(limit=5)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["summary"]["expiredCount"] == 1
    assert result["summary"]["dispatchedCount"] == 0
    assert result["expired"][0]["preflight"]["status"] == "failed"
    assert "vaultBalance=0" in result["expired"][0]["confirmResult"]["failureReason"]
    assert fetch_execution_status(execution_id) == "failed"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"
