import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import api_server
from scripts import blockchain_sync
from scripts import execution_messages
from scripts import internal_api
from scripts import matching_service
from scripts import orchestrator_server
from scripts.internal_api import app


client = TestClient(app)
TEST_INTERNAL_TOKEN = "test-internal-token"


@pytest.fixture(autouse=True)
def isolated_internal_api_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個 internal API 測試都使用暫存 DB 與測試 token。"""
    data_dir = tmp_path / "databases"

    monkeypatch.setenv("INTERNAL_API_TOKEN", TEST_INTERNAL_TOKEN)

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


def auth_headers() -> dict[str, str]:
    """建立 internal API 測試 header。"""
    return {"X-Internal-Token": TEST_INTERNAL_TOKEN}


def insert_buy_order() -> int:
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
                updated_at,
                intent_json,
                signature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "buyer",
                "free",
                "WETH",
                1,
                1,
                3000,
                1,
                0.3,
                "pending",
                0,
                now,
                now,
                '{"user":"0xBuyer","tokenIn":"0xUSDC","tokenOut":"0xWETH","amountIn":"3000","minAmountOut":"1","deadline":1999999999,"salt":"0xbuy","allowPartialFill":true}',
                "0xbuy_signature",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order() -> int:
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
                queue_at,
                intent_json,
                signature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seller",
                "free",
                "WETH",
                1,
                1,
                2900,
                1,
                0.3,
                "pending",
                0,
                now,
                now,
                now,
                '{"user":"0xSeller","tokenIn":"0xWETH","tokenOut":"0xUSDC","amountIn":"1","minAmountOut":"2900","deadline":1999999999,"salt":"0xsell","allowPartialFill":true}',
                "0xsell_signature",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_internal_api_rejects_missing_token() -> None:
    """測試 internal API 沒有 token 時拒絕。"""
    response = client.get("/internal/executions/pending")

    assert response.status_code == 401


def test_internal_api_defaults_to_grok_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 internal matching API 預設使用 Grok 並 drain 到沒有可派發賣單。"""
    captured: dict[str, object] = {}

    def fake_run_matching_drain(agent: str, candidate_limit: int, max_cycles: int):
        captured.update(
            {
                "agent": agent,
                "candidate_limit": candidate_limit,
                "max_cycles": max_cycles,
            }
        )
        return {
            "status": "matching_drain_completed",
            "agent": agent,
            "stopReason": "no_processable_sell_order",
            "cyclesRun": 0,
            "cycles": [],
        }

    monkeypatch.setattr(matching_service, "run_matching_drain", fake_run_matching_drain)

    response = client.post("/internal/matching/run", headers=auth_headers(), json={})

    assert response.status_code == 200
    assert response.json()["status"] == "matching_drain_completed"
    assert captured == {
        "agent": "grok",
        "candidate_limit": 5,
        "max_cycles": 100,
    }


def test_internal_api_full_backend_to_chain_message_flow() -> None:
    """測試 internal API 可跑媒合、取 payload、dispatch、回報 confirmed。"""
    buy_id = insert_buy_order()
    sell_id = insert_sell_order()

    match_response = client.post(
        "/internal/matching/run",
        headers=auth_headers(),
        json={"agent": "main-brain", "candidate_limit": 5, "drain_until_empty": False},
    )
    pending_response = client.get(
        "/internal/executions/pending",
        headers=auth_headers(),
        params={"ready_only": True},
    )
    execution_id = pending_response.json()[0]["executionId"]
    get_response = client.get(f"/internal/executions/{execution_id}", headers=auth_headers())
    dispatch_response = client.post(
        f"/internal/executions/{execution_id}/dispatch",
        headers=auth_headers(),
        json={"dispatch_metadata": {"target": "chain-worker-http"}},
    )
    result_response = client.post(
        f"/internal/executions/{execution_id}/result",
        headers=auth_headers(),
        json={"status": "confirmed", "tx_hash": "0xhttp", "block_number": 456},
    )

    with sqlite3.connect(orchestrator_server.BUY_ORDERS_DB) as conn:
        buy = conn.execute("SELECT remaining_amount, status FROM buy_orders WHERE id = ?", (buy_id,)).fetchone()
    with sqlite3.connect(orchestrator_server.SELL_ORDERS_DB) as conn:
        sell = conn.execute("SELECT remaining_amount, status FROM sell_orders WHERE id = ?", (sell_id,)).fetchone()

    assert match_response.status_code == 200
    assert match_response.json()["runnerResult"]["status"] == "execution_proposed"
    assert pending_response.status_code == 200
    assert pending_response.json()[0]["readyForExecutor"] is True
    assert pending_response.json()[0]["payload"]["actionType"] == 1
    assert set(pending_response.json()[0]["payload"]) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert set(pending_response.json()[0]["payload"]["routeDetails"]) == {"Calldata", "matchedIntentB", "treasuryAmountOut"}
    assert get_response.status_code == 200
    assert get_response.json()["executionId"] == execution_id
    assert get_response.json()["payload"] == pending_response.json()[0]["payload"]
    assert dispatch_response.status_code == 200
    assert dispatch_response.json()["executionStatus"] == "dispatched"
    assert result_response.status_code == 200
    assert result_response.json()["executionStatus"] == "confirmed"
    assert buy == (0, "filled")
    assert sell == (0, "filled")


def test_internal_api_failed_result_does_not_update_orders() -> None:
    """測試 internal API 收到 failed 結果時不扣訂單。"""
    buy_id = insert_buy_order()
    sell_id = insert_sell_order()

    client.post(
        "/internal/matching/run",
        headers=auth_headers(),
        json={"agent": "main-brain", "candidate_limit": 5, "drain_until_empty": False},
    )
    execution_id = client.get(
        "/internal/executions/pending",
        headers=auth_headers(),
        params={"ready_only": True},
    ).json()[0]["executionId"]
    client.post(f"/internal/executions/{execution_id}/dispatch", headers=auth_headers(), json={})
    response = client.post(
        f"/internal/executions/{execution_id}/result",
        headers=auth_headers(),
        json={"status": "failed", "failure_reason": "chain reverted"},
    )

    with sqlite3.connect(orchestrator_server.BUY_ORDERS_DB) as conn:
        buy = conn.execute("SELECT remaining_amount, status FROM buy_orders WHERE id = ?", (buy_id,)).fetchone()
    with sqlite3.connect(orchestrator_server.SELL_ORDERS_DB) as conn:
        sell = conn.execute("SELECT remaining_amount, status FROM sell_orders WHERE id = ?", (sell_id,)).fetchone()

    assert response.status_code == 200
    assert response.json()["executionStatus"] == "failed"
    assert buy == (1, "pending")
    assert sell == (1, "pending")


def test_internal_api_can_dispatch_execution_to_keeperhub(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 internal API 可把 execution payload 轉送到 KeeperHub webhook。"""
    buy_id = insert_buy_order()
    sell_id = insert_sell_order()
    captured: dict[str, object] = {}

    client.post(
        "/internal/matching/run",
        headers=auth_headers(),
        json={"agent": "main-brain", "candidate_limit": 5, "drain_until_empty": False},
    )
    execution_id = client.get(
        "/internal/executions/pending",
        headers=auth_headers(),
        params={"ready_only": True},
    ).json()[0]["executionId"]

    def fake_send_execution_to_keeperhub(
        execution_id_arg,
        webhook_url,
        timeout_seconds,
        webhook_headers,
        wait_for_final_result,
        poll_interval_seconds,
        max_wait_seconds,
        status_api_base,
        status_headers,
    ):
        captured["execution_id"] = execution_id_arg
        captured["webhook_url"] = webhook_url
        captured["timeout_seconds"] = timeout_seconds
        captured["webhook_headers"] = webhook_headers
        captured["wait_for_final_result"] = wait_for_final_result
        captured["poll_interval_seconds"] = poll_interval_seconds
        captured["max_wait_seconds"] = max_wait_seconds
        captured["status_api_base"] = status_api_base
        captured["status_headers"] = status_headers
        return {
            "status": "keeperhub_dispatch_completed",
            "executionId": execution_id_arg,
            "executionStatus": "dispatched",
            "keeperhub": {"httpStatusCode": 202, "body": {"accepted": True}},
        }

    monkeypatch.setattr(execution_messages, "send_execution_to_keeperhub", fake_send_execution_to_keeperhub)

    response = client.post(
        f"/internal/executions/{execution_id}/keeperhub/dispatch",
        headers=auth_headers(),
        json={
            "webhook_url": "https://example.com/webhook",
            "timeout_seconds": 9,
            "webhook_headers": {"Authorization": "Bearer test"},
            "wait_for_final_result": True,
            "poll_interval_seconds": 2,
            "max_wait_seconds": 20,
            "status_api_base": "https://example.com/executions",
            "status_headers": {"Authorization": "Bearer status-token"},
        },
    )

    assert response.status_code == 200
    assert response.json()["executionStatus"] == "dispatched"
    assert captured == {
        "execution_id": execution_id,
        "webhook_url": "https://example.com/webhook",
        "timeout_seconds": 9,
        "webhook_headers": {"Authorization": "Bearer test"},
        "wait_for_final_result": True,
        "poll_interval_seconds": 2,
        "max_wait_seconds": 20,
        "status_api_base": "https://example.com/executions",
        "status_headers": {"Authorization": "Bearer status-token"},
    }
    assert buy_id > 0
    assert sell_id > 0


def test_internal_api_can_refresh_keeperhub_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 internal API 可觸發 KeeperHub running execution 自動收尾。"""
    captured: dict[str, object] = {}

    def fake_refresh_keeperhub_execution_results(limit, timeout_seconds, status_api_base, status_headers):
        captured["limit"] = limit
        captured["timeout_seconds"] = timeout_seconds
        captured["status_api_base"] = status_api_base
        captured["status_headers"] = status_headers
        return {
            "status": "keeperhub_refresh_completed",
            "checkedCount": 1,
            "waiting": [],
            "finalized": [{"executionId": "execution:1:match:1", "executionStatus": "confirmed"}],
            "skipped": [],
            "errors": [],
        }

    monkeypatch.setattr(execution_messages, "refresh_keeperhub_execution_results", fake_refresh_keeperhub_execution_results)

    response = client.post(
        "/internal/executions/keeperhub/refresh",
        headers=auth_headers(),
        json={
            "limit": 3,
            "timeout_seconds": 11,
            "status_api_base": "https://example.com/executions",
            "status_headers": {"Authorization": "Bearer status-token"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "keeperhub_refresh_completed"
    assert response.json()["finalized"][0]["executionStatus"] == "confirmed"
    assert captured == {
        "limit": 3,
        "timeout_seconds": 11,
        "status_api_base": "https://example.com/executions",
        "status_headers": {"Authorization": "Bearer status-token"},
    }
