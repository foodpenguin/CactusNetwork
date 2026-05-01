import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import api_server
from scripts import execution_messages
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_execution_message_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個 execution message 測試都使用暫存 DB。"""
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


def create_proposed_execution(payload: dict) -> tuple[str, int, int]:
    """建立 proposed execution 測試資料。"""
    buy_id = insert_buy_order()
    sell_id = insert_sell_order()
    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1,
                    "unitPriceUsdc": 2900,
                }
            ],
            "executionPayload": payload,
        }
    )
    return result["executionId"], buy_id, sell_id


def make_ready_blockchain_payload() -> dict:
    """建立符合區塊鏈端嚴格格式的 payload。"""
    return {
        "intentA": {
            "intent": {
                "user": "0xSeller",
                "tokenIn": "0xWETH",
                "tokenOut": "0xUSDC",
                "amountIn": "1",
                "minAmountOut": "2900",
                "deadline": 1999999999,
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
                    "deadline": 1999999999,
                    "salt": "0xbuy",
                    "allowPartialFill": True,
                },
                "signature": "0xbuy_signature",
                "executeAmountInB": "2900",
            },
            "treasuryAmountOut": None,
        },
    }


def make_ready_dex_payload() -> dict:
    """建立 actionType 0 使用的 DEX payload。"""
    payload = make_ready_blockchain_payload()
    payload["actionType"] = 0
    payload["routeDetails"] = {
        "Calldata": "0x04e45aaf",
        "matchedIntentB": None,
        "treasuryAmountOut": None,
    }
    return payload


def fetch_order_remaining(db_path: Path, table_name: str, order_id: int) -> tuple[float, str]:
    """讀取訂單剩餘量與狀態。"""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT remaining_amount, status FROM {table_name} WHERE id = ?",
            (order_id,),
        ).fetchone()
    assert row is not None
    return row


def test_get_pending_execution_requests_returns_formatted_payload() -> None:
    """測試區塊鏈端可取得待處理嚴格 payload。"""
    payload = make_ready_blockchain_payload()
    expected_payload = make_ready_blockchain_payload()
    expected_payload["routeDetails"]["matchedIntentB"]["executeAmountInB"] = "1450"
    execution_id, _, _ = create_proposed_execution(payload)

    requests = execution_messages.get_pending_execution_requests(ready_only=True)
    request = execution_messages.get_execution_request(execution_id)

    assert len(requests) == 1
    assert requests[0]["executionId"] == execution_id
    assert requests[0]["readyForExecutor"] is True
    assert requests[0]["payload"] == expected_payload
    assert set(requests[0]["payload"]) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert set(requests[0]["payload"]["routeDetails"]) == {"Calldata", "matchedIntentB", "treasuryAmountOut"}
    assert request["executionId"] == execution_id
    assert request["payload"] == expected_payload


def test_action_type_zero_requires_calldata_to_be_ready() -> None:
    """測試 DEX 類 payload 必須有 Calldata 才會被視為可送出。"""
    ready_payload = make_ready_dex_payload()
    missing_calldata_payload = make_ready_dex_payload()
    missing_calldata_payload["routeDetails"]["Calldata"] = None

    ready_execution_id, _, _ = create_proposed_execution(ready_payload)
    create_proposed_execution(missing_calldata_payload)

    requests = execution_messages.get_pending_execution_requests(ready_only=True)
    all_requests = execution_messages.get_pending_execution_requests(ready_only=False)

    assert [request["executionId"] for request in requests] == [ready_execution_id]
    assert all_requests[0]["missingFields"] == []
    assert all_requests[1]["missingFields"] == ["routeDetails.Calldata"]


def test_dispatched_execution_can_be_confirmed_and_updates_orders_once() -> None:
    """測試 dispatched 後收到 confirmed，才更新買賣單，重複回報不會重複扣單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())

    dispatch = execution_messages.mark_execution_dispatched(execution_id, {"target": "chain-worker"})
    result = execution_messages.submit_execution_result(
        execution_id,
        {"status": "confirmed", "tx_hash": "0xtx", "block_number": 123},
    )
    duplicate = execution_messages.submit_execution_result(
        execution_id,
        {"status": "confirmed", "tx_hash": "0xtx", "block_number": 123},
    )

    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert dispatch["executionStatus"] == "dispatched"
    assert result["executionStatus"] == "confirmed"
    assert duplicate["status"] == "already_finalized"
    assert buy_remaining == 1
    assert buy_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"


def test_failed_execution_result_does_not_update_orders() -> None:
    """測試區塊鏈端回報 failed 時不扣買賣單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())
    execution_messages.mark_execution_dispatched(execution_id)

    result = execution_messages.submit_execution_result(
        execution_id,
        {"status": "failed", "failure_reason": "estimateGas failed"},
    )

    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["executionStatus"] == "failed"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"


def test_send_execution_to_keeperhub_posts_payload_and_accepts_confirmed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試可將嚴格 payload 送到 KeeperHub，並在回覆 confirmed 時正式扣單。"""
    payload = make_ready_blockchain_payload()
    expected_payload = make_ready_blockchain_payload()
    expected_payload["routeDetails"]["matchedIntentB"]["executeAmountInB"] = "1450"
    execution_id, buy_id, sell_id = create_proposed_execution(payload)
    captured: dict[str, object] = {}

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        captured["url"] = url
        captured["payload"] = posted_payload
        captured["timeout_seconds"] = timeout_seconds
        captured["extra_headers"] = extra_headers
        return {
            "httpStatusCode": 200,
            "body": {
                "status": "confirmed",
                "tx_hash": "0xkeeper",
                "block_number": 789,
            },
        }

    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_messages.send_execution_to_keeperhub(execution_id, timeout_seconds=12)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert captured["url"] == execution_messages.DEFAULT_KEEPERHUB_WEBHOOK_URL
    assert captured["payload"] == expected_payload
    assert captured["timeout_seconds"] == 12
    assert captured["extra_headers"] == {}
    assert result["status"] == "keeperhub_result_accepted"
    assert result["executionStatus"] == "confirmed"
    assert result["keeperhub"]["body"]["tx_hash"] == "0xkeeper"
    assert buy_remaining == 1
    assert buy_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"


def test_send_execution_to_keeperhub_keeps_dispatched_when_response_is_not_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """測試 KeeperHub 只回覆接收成功時，後端只標記 dispatched，不扣訂單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())

    def fake_post_json(url: str, posted_payload: dict, timeout_seconds: float, extra_headers: dict) -> dict:
        return {"httpStatusCode": 202, "body": {"accepted": True, "requestId": "keeper-1"}}

    monkeypatch.setattr(execution_messages, "_post_json", fake_post_json)

    result = execution_messages.send_execution_to_keeperhub(
        execution_id,
        webhook_url="https://example.com/webhook",
    )
    request = execution_messages.get_execution_request(execution_id)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["status"] == "keeperhub_dispatch_completed"
    assert result["executionStatus"] == "dispatched"
    assert request["status"] == "dispatched"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"


def test_refresh_keeperhub_execution_results_keeps_running_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 KeeperHub 還在 running 時，後端不結束、不扣單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())
    execution_messages.mark_execution_dispatched(
        execution_id,
        {"target": "keeperhub", "webhookResponse": {"id": "kh-running", "status": "running"}},
    )
    captured: dict[str, object] = {}

    def fake_get_json(url: str, timeout_seconds: float, extra_headers: dict) -> dict:
        captured["url"] = url
        captured["timeout_seconds"] = timeout_seconds
        captured["extra_headers"] = extra_headers
        return {"id": "kh-running", "status": "running"}

    monkeypatch.setattr(execution_messages, "_get_json", fake_get_json)

    result = execution_messages.refresh_keeperhub_execution_results(limit=5, timeout_seconds=8)
    request = execution_messages.get_execution_request(execution_id)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert captured == {
        "url": f"{execution_messages.DEFAULT_KEEPERHUB_STATUS_API_BASE}/kh-running/status",
        "timeout_seconds": 8,
        "extra_headers": {},
    }
    assert result["checkedCount"] == 1
    assert result["waiting"][0]["executionId"] == execution_id
    assert result["finalized"] == []
    assert request["status"] == "dispatched"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"


def test_refresh_keeperhub_execution_results_confirms_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 KeeperHub success 會正式收尾，重複刷新不會重複扣單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())
    execution_messages.mark_execution_dispatched(
        execution_id,
        {"target": "keeperhub", "webhookResponse": {"executionId": "kh-success", "status": "running"}},
    )

    def fake_get_json(url: str, timeout_seconds: float, extra_headers: dict) -> dict:
        return {"id": "kh-success", "status": "success", "txHash": "0xkeeper", "blockNumber": 55}

    monkeypatch.setattr(execution_messages, "_get_json", fake_get_json)

    result = execution_messages.refresh_keeperhub_execution_results(limit=5)
    duplicate = execution_messages.refresh_keeperhub_execution_results(limit=5)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["finalized"][0]["executionId"] == execution_id
    assert result["finalized"][0]["executionStatus"] == "confirmed"
    assert duplicate["checkedCount"] == 0
    assert buy_remaining == 1
    assert buy_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"


def test_wait_for_keeperhub_execution_result_polls_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 wait 模式會跳過 running，直到 success 才結束並扣單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())
    execution_messages.mark_execution_dispatched(
        execution_id,
        {"target": "keeperhub", "webhookResponse": {"id": "kh-wait", "status": "running"}},
    )
    statuses = iter(
        [
            {"id": "kh-wait", "status": "running"},
            {"id": "kh-wait", "status": "success", "txHash": "0xwait", "blockNumber": 77},
        ]
    )

    def fake_get_json(url: str, timeout_seconds: float, extra_headers: dict) -> dict:
        return next(statuses)

    monkeypatch.setattr(execution_messages, "_get_json", fake_get_json)
    monkeypatch.setattr(execution_messages.time, "sleep", lambda seconds: None)

    result = execution_messages.wait_for_keeperhub_execution_result(
        execution_id,
        poll_interval_seconds=0.1,
        max_wait_seconds=5,
    )
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["status"] == "keeperhub_final_result_accepted"
    assert result["executionStatus"] == "confirmed"
    assert buy_remaining == 1
    assert buy_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"


def test_refresh_keeperhub_execution_results_marks_failed_without_deducting(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 KeeperHub failed / error 只讓 execution 失敗，不扣訂單。"""
    execution_id, buy_id, sell_id = create_proposed_execution(make_ready_blockchain_payload())
    execution_messages.mark_execution_dispatched(
        execution_id,
        {"target": "keeperhub", "webhookResponse": {"workflowExecutionId": "kh-failed", "status": "running"}},
    )

    def fake_get_json(url: str, timeout_seconds: float, extra_headers: dict) -> dict:
        return {"id": "kh-failed", "status": "error", "error": "router reverted"}

    monkeypatch.setattr(execution_messages, "_get_json", fake_get_json)

    result = execution_messages.refresh_keeperhub_execution_results(limit=5)
    buy_remaining, buy_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert result["finalized"][0]["executionStatus"] == "failed"
    assert buy_remaining == 2
    assert buy_status == "pending"
    assert sell_remaining == 1
    assert sell_status == "pending"


def test_refresh_keeperhub_split_executions_merge_into_same_sell_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試拆單成多筆 execution 後，各自 confirmed 會合併更新同一張賣單。"""
    buy_id_1 = insert_buy_order(amount=2)
    buy_id_2 = insert_buy_order(amount=2)
    sell_id = insert_sell_order(amount=3)
    proposal = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "executionId": "split-sell",
            "sellOrderId": sell_id,
            "matches": [
                {"buyOrderId": buy_id_1, "filledAmount": 1.25, "unitPriceUsdc": 2900},
                {"buyOrderId": buy_id_2, "filledAmount": 1.75, "unitPriceUsdc": 2900},
            ],
            "executionPayload": make_ready_blockchain_payload(),
        }
    )
    execution_ids = proposal["executionIds"]
    execution_messages.mark_execution_dispatched(
        execution_ids[0],
        {"target": "keeperhub", "webhookResponse": {"id": "kh-split-1", "status": "running"}},
    )
    execution_messages.mark_execution_dispatched(
        execution_ids[1],
        {"target": "keeperhub", "webhookResponse": {"id": "kh-split-2", "status": "running"}},
    )

    def fake_get_json(url: str, timeout_seconds: float, extra_headers: dict) -> dict:
        keeperhub_id = url.rstrip("/").split("/")[-2]
        return {"id": keeperhub_id, "status": "success", "txHash": f"0x{keeperhub_id}", "blockNumber": 66}

    monkeypatch.setattr(execution_messages, "_get_json", fake_get_json)

    result = execution_messages.refresh_keeperhub_execution_results(limit=5)
    buy_1_remaining, buy_1_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id_1)
    buy_2_remaining, buy_2_status = fetch_order_remaining(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_id_2)
    sell_remaining, sell_status = fetch_order_remaining(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_id)

    assert [item["executionStatus"] for item in result["finalized"]] == ["confirmed", "confirmed"]
    assert buy_1_remaining == 0.75
    assert buy_1_status == "pending"
    assert buy_2_remaining == 0.25
    assert buy_2_status == "pending"
    assert sell_remaining == 0
    assert sell_status == "filled"
