import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts import agent_runner
from scripts import api_server
from scripts import blockchain_sync
from scripts import main_brain
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_integration_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """讓 runner integration test 使用暫存 DB，不碰本機實際資料。"""
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

    monkeypatch.setattr(blockchain_sync, "DATA_DIR", data_dir)
    monkeypatch.setattr(blockchain_sync, "ONCHAIN_STATE_DB", data_dir / "onchain_state.db")
    monkeypatch.setattr(blockchain_sync, "EXTERNAL_CONTRACTS_DB", data_dir / "external_contracts.db")

    api_server.SESSIONS.clear()
    api_server._init_databases()
    orchestrator_server._ensure_databases()
    blockchain_sync._init_database()
    blockchain_sync._init_external_contracts_database()


def iso_now() -> str:
    """產生 UTC ISO 時間字串。"""
    return datetime.now(timezone.utc).isoformat()


def insert_buy_order(
    *,
    account_name: str = "buyer",
    asset: str = "WETH",
    amount: float = 2,
    max_price: float = 3000,
) -> int:
    """新增一筆測試買單，回傳 buy_order id。"""
    now = iso_now()
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
            (
                account_name,
                "free",
                asset,
                amount,
                amount,
                max_price,
                3,
                0.3,
                "pending",
                0,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order(
    *,
    account_name: str = "seller",
    asset: str = "WETH",
    amount: float = 1,
    min_price: float = 2900,
) -> int:
    """新增一筆測試賣單，回傳 sell_order id。"""
    now = iso_now()
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
            (
                account_name,
                "free",
                asset,
                amount,
                amount,
                min_price,
                3,
                0.3,
                "pending",
                0,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def count_rows(db_path: Path, table_name: str) -> int:
    """計算指定 table 目前有幾筆資料。"""
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def fetch_order_row(db_path: Path, table_name: str, order_id: int) -> sqlite3.Row:
    """讀取一筆訂單資料，讓測試可以檢查 runner 套用結果。"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT * FROM {table_name} WHERE id = ?", (order_id,)).fetchone()
    assert row is not None
    return row


def internal_first_black_box_agent(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """模擬主腦黑盒：本地候選存在時提出成交單，不要求外部資料。"""
    assert external_context is None
    sell_order = task["sellOrder"]
    candidate = task["candidateBuyOrders"][0]
    return {
        "taskId": task["taskId"],
        "decisionStatus": "proposed_execution",
        "sellOrderId": sell_order["id"],
        "matches": [
            {
                "buyOrderId": candidate["id"],
                "filledAmount": sell_order["remainingAmount"],
                "unitPriceUsdc": sell_order["minUnitPriceUsdc"],
            }
        ],
        "agentNotes": "黑盒主腦：內部候選可撮合，提出成交單，無需查外部合約",
    }


def external_needed_black_box_agent(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """模擬主腦黑盒：本地沒有可用候選時，先要求外部資料，再給最終決策。"""
    sell_order = task["sellOrder"]
    if external_context is None:
        assert task["candidateBuyOrders"] == []
        return {
            "taskId": task["taskId"],
            "decisionStatus": "request_external_contract_data",
            "sellOrderId": sell_order["id"],
            "reason": "黑盒主腦：本地候選不足，要求查外部合約",
            "externalQuery": {
                "sourceOrderType": "sell",
                "syncTargets": [
                    {
                        "intentId": "external-intent-for-integration-test",
                        "intentHash": "0x" + "11" * 32,
                        "user": "0x" + "22" * 20,
                        "tokenIn": "0x" + "33" * 20,
                        "tokenOut": "0x" + "44" * 20,
                        "amountIn": "10",
                    }
                ],
            },
        }

    assert external_context["candidates"][0]["candidate"]["reads"]["remainingAmountIn"] == "7"
    return {
        "taskId": task["taskId"],
        "decisionStatus": "rejected",
        "sellOrderId": sell_order["id"],
        "failureReason": "黑盒主腦：外部資料已取得，但本輪仍不成交",
    }


def test_runner_does_not_query_external_contracts_when_internal_match_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整合測試：內部買賣可撮合時，runner 只提出成交單，不查外部合約。"""
    buy_id = insert_buy_order(max_price=3100)
    sell_id = insert_sell_order(min_price=2900)

    def fail_if_external_contract_data_is_requested(*args, **kwargs):
        raise AssertionError("內部可撮合時不應呼叫外部合約資料查詢")

    monkeypatch.setattr(
        blockchain_sync,
        "request_external_contract_data",
        fail_if_external_contract_data_is_requested,
    )

    result = agent_runner.run_agent_cycle(internal_first_black_box_agent)

    buy = fetch_order_row(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell = fetch_order_row(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["status"] == "execution_proposed"
    assert result["externalRequest"] is None
    assert result["externalContext"] is None
    assert result["applyResult"]["decisionStatus"] == "proposed_execution"
    assert result["applyResult"]["executionStatus"] == "proposed"
    assert buy["remaining_amount"] == 2
    assert buy["status"] == "pending"
    assert sell["remaining_amount"] == 1
    assert sell["status"] == "pending"
    assert count_rows(blockchain_sync.EXTERNAL_CONTRACTS_DB, "external_contract_queries") == 0
    assert count_rows(blockchain_sync.EXTERNAL_CONTRACTS_DB, "external_contract_candidates") == 0
    assert count_rows(orchestrator_server.EXECUTIONS_DB, "executions") == 1


def test_runner_can_use_main_brain_and_record_document_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整合測試：runner 可直接使用 main_brain.decide 並記錄嚴格區塊鏈 payload。"""
    buy_id = insert_buy_order(max_price=3100)
    sell_id = insert_sell_order(min_price=2900)

    def fail_if_external_contract_data_is_requested(*args, **kwargs):
        raise AssertionError("main_brain 內部可撮合時不應查外部合約")

    monkeypatch.setattr(
        blockchain_sync,
        "request_external_contract_data",
        fail_if_external_contract_data_is_requested,
    )

    result = agent_runner.run_agent_cycle(main_brain.decide)

    buy = fetch_order_row(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell = fetch_order_row(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)
    execution_payload = result["applyResult"]["executionPayload"]

    assert result["status"] == "execution_proposed"
    assert result["applyResult"]["executionStatus"] == "proposed"
    assert set(execution_payload) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert set(execution_payload["routeDetails"]) == {"Calldata", "matchedIntentB", "treasuryAmountOut"}
    assert execution_payload["actionType"] == 1
    assert execution_payload["intentA"]["signature"] is None
    assert buy["remaining_amount"] == 2
    assert sell["remaining_amount"] == 1
    assert count_rows(orchestrator_server.EXECUTIONS_DB, "executions") == 1


def test_runner_records_external_context_then_applies_final_black_box_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整合測試：內部不可撮合時，runner 會查外部資料並套用黑盒主腦最終決策。"""
    insert_buy_order(account_name="local_buyer_below_price", max_price=2500)
    sell_id = insert_sell_order(min_price=2900)

    def fake_read_uniswap_target(target_data: dict[str, Any], rpc_url: Optional[str] = None) -> dict[str, Any]:
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "fake_black_box_integration_test",
            "intent": {
                "intentHash": target_data["intentHash"],
                "user": target_data["user"],
                "tokenIn": target_data["tokenIn"],
                "tokenOut": target_data["tokenOut"],
                "amountIn": target_data["amountIn"],
            },
            "reads": {
                "vaultBalance": "7",
                "filledAmountIn": "3",
                "remainingAmountIn": "7",
                "treasuryBalance": "100",
            },
            "skipped": [],
            "errors": [],
            "isValid": True,
        }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    result = agent_runner.run_agent_cycle(external_needed_black_box_agent)

    sell = fetch_order_row(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["status"] == "completed_after_external_context"
    assert result["agentDecision"]["decisionStatus"] == "request_external_contract_data"
    assert result["externalRequestRecord"]["status"] == "external_contract_data_requested"
    assert result["externalRequest"]["checked"] == 1
    assert result["externalContext"]["candidates"][0]["isValid"] is True
    assert result["finalAgentDecision"]["decisionStatus"] == "rejected"
    assert result["applyResult"]["sellOrderStatus"] == "pending"
    assert sell["attempts"] == 1
    assert sell["status"] == "pending"
    assert count_rows(blockchain_sync.EXTERNAL_CONTRACTS_DB, "external_contract_queries") == 1
    assert count_rows(blockchain_sync.EXTERNAL_CONTRACTS_DB, "external_contract_snapshots") == 1
    assert count_rows(blockchain_sync.EXTERNAL_CONTRACTS_DB, "external_contract_candidates") == 1
    assert count_rows(blockchain_sync.ONCHAIN_STATE_DB, "onchain_states") == 1
