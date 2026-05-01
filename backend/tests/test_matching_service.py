import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import api_server
from scripts import blockchain_sync
from scripts import matching_service
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_matching_service_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個 matching service 測試都使用暫存 DB。"""
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

    api_server._init_databases()
    orchestrator_server._ensure_databases()
    blockchain_sync._init_database()
    blockchain_sync._init_external_contracts_database()


def insert_buy_order(account_name: str = "buyer") -> int:
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
            (account_name, "free", "WETH", 2, 2, 3000, 3, 0.3, "pending", 0, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order(account_name: str = "seller") -> int:
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
            (account_name, "free", "WETH", 1, 1, 2900, 3, 0.3, "pending", 0, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_run_matching_once_uses_main_brain_and_creates_execution_request() -> None:
    """測試後端媒合入口可跑一輪並產生 execution 提案。"""
    insert_buy_order()
    insert_sell_order()

    result = matching_service.run_matching_once(agent="main-brain", candidate_limit=5)

    assert result["status"] == "matching_cycle_completed"
    assert result["timeoutRefresh"]["buyTimedOut"] == 0
    assert result["runnerResult"]["status"] == "execution_proposed"

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        execution_count = conn.execute("SELECT COUNT(*) FROM executions WHERE status = 'proposed'").fetchone()[0]

    assert execution_count == 1


def test_run_matching_once_reports_no_task_when_queue_is_empty() -> None:
    """測試沒有 pending 賣單時，媒合入口回傳 no_task。"""
    result = matching_service.run_matching_once(agent="main-brain", candidate_limit=5)

    assert result["status"] == "matching_cycle_completed"
    assert result["runnerResult"]["status"] == "no_task"


def test_run_matching_defaults_to_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試正式媒合預設使用 Grok 主腦。"""
    selected_agents: list[str] = []

    def fake_select_agent(agent: str):
        selected_agents.append(agent)

        def fake_agent(task, external_context):
            return {
                "taskId": task["taskId"],
                "decisionStatus": "invalid",
                "sellOrderId": task["sellOrder"]["id"],
                "failureReason": "測試預設 agent，不呼叫真實 Grok",
            }

        return fake_agent

    insert_sell_order()
    monkeypatch.setattr(matching_service, "_select_agent_decide", fake_select_agent)

    result = matching_service.run_matching_once()

    assert selected_agents == ["grok"]
    assert result["agent"] == "grok"


def test_run_matching_drain_processes_until_no_processable_sell_order() -> None:
    """測試 drain 會連續處理賣單，直到剩下的賣單都在等待 execution 回覆。"""
    insert_buy_order("buyer-1")
    insert_buy_order("buyer-2")
    insert_sell_order("seller-1")
    insert_sell_order("seller-2")

    result = matching_service.run_matching_drain(agent="main-brain", candidate_limit=5, max_cycles=5)

    assert result["status"] == "matching_drain_completed"
    assert result["agent"] == "main-brain"
    assert result["stopReason"] == "no_processable_sell_order"
    assert result["cyclesRun"] == 3
    assert [cycle["runnerResult"]["status"] for cycle in result["cycles"]] == [
        "execution_proposed",
        "execution_proposed",
        "no_task",
    ]

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        execution_count = conn.execute("SELECT COUNT(*) FROM executions WHERE status = 'proposed'").fetchone()[0]

    assert execution_count == 2
