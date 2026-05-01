import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from scripts import api_server
from scripts import orchestrator_server


@pytest.fixture(autouse=True)
def isolated_orchestrator_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個中控測試都使用獨立暫存 DB，避免影響本機實際測試資料。"""
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
    monkeypatch.setattr(orchestrator_server, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(orchestrator_server, "ORDER_TIMEOUT_MINUTES", 15)

    api_server.SESSIONS.clear()
    api_server._init_databases()
    orchestrator_server._ensure_databases()

    with sqlite3.connect(orchestrator_server.ORCHESTRATOR_STATE_DB) as conn:
        conn.execute("UPDATE state SET value = '0' WHERE key = 'weighted_index'")
        conn.commit()


def iso_now(offset_minutes: int = 0) -> str:
    """產生 UTC ISO 時間字串，讓測試資料可控制 queue 與 timeout 時間。"""
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).isoformat()


def insert_buy_order(
    *,
    account_name: str = "buyer",
    level: str = "free",
    asset: str = "WETH",
    amount: float = 10,
    max_price: float = 3000,
    status: str = "pending",
    attempts: int = 0,
    created_at: Optional[str] = None,
    intent_json: Optional[dict] = None,
    signature: Optional[str] = None,
) -> int:
    """直接插入買單測試資料，回傳 buy_order id。"""
    created_at = created_at or iso_now()
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
                account_name,
                level,
                asset,
                amount,
                amount,
                max_price,
                3,
                0.3,
                status,
                attempts,
                created_at,
                created_at,
                json.dumps(intent_json, ensure_ascii=False, sort_keys=True) if intent_json is not None else None,
                signature,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_sell_order(
    *,
    account_name: str = "seller",
    level: str = "free",
    asset: str = "WETH",
    amount: float = 1,
    min_price: float = 2900,
    status: str = "pending",
    attempts: int = 0,
    created_at: Optional[str] = None,
    queue_at: Optional[str] = None,
    intent_json: Optional[dict] = None,
    signature: Optional[str] = None,
) -> int:
    """直接插入賣單測試資料，回傳 sell_order id。"""
    created_at = created_at or iso_now()
    queue_at = queue_at or created_at
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
                account_name,
                level,
                asset,
                amount,
                amount,
                min_price,
                3,
                0.3,
                status,
                attempts,
                created_at,
                created_at,
                queue_at,
                json.dumps(intent_json, ensure_ascii=False, sort_keys=True) if intent_json is not None else None,
                signature,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def fetch_one(db_path: Path, query: str, params: tuple = ()) -> sqlite3.Row:
    """用 Row 格式讀取單筆資料，讓測試可用欄位名稱檢查結果。"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, params).fetchone()
    assert row is not None
    return row


def fetch_decision(task_id: str) -> sqlite3.Row:
    """讀取一筆 decisions 紀錄，讓測試確認 prepare/apply 都有被記錄。"""
    return fetch_one(
        orchestrator_server.DECISIONS_DB,
        "SELECT * FROM decisions WHERE task_id = ?",
        (task_id,),
    )


def make_intent(user: str, amount_in: str, min_amount_out: str, salt: str) -> dict:
    """建立測試用 intent JSON。"""
    return {
        "user": user,
        "tokenIn": "0xTokenIn",
        "tokenOut": "0xTokenOut",
        "amountIn": amount_in,
        "minAmountOut": min_amount_out,
        "deadline": 1999999999,
        "salt": "0x" + salt * 32,
        "allowPartialFill": True,
    }


def test_status_reports_sell_queues_and_buy_pending() -> None:
    """測試 status 以賣單佇列為主，不再回傳 buyQueues。"""
    insert_buy_order(account_name="buyer_a")
    insert_buy_order(account_name="buyer_b")
    insert_sell_order(account_name="admin_seller", level="admin")
    insert_sell_order(account_name="max_seller", level="max")
    insert_sell_order(account_name="plus_seller", level="plus")
    insert_sell_order(account_name="free_seller", level="free")

    status = orchestrator_server.get_queue_status()

    assert "buyQueues" not in status
    assert status["sellQueues"] == {"admin": 1, "max": 1, "plus": 1, "free": 1}
    assert status["buyPending"] == 2
    assert status["timeoutOrders"] == 0


def test_process_batch_prioritizes_sell_queue_levels() -> None:
    """測試 process-batch 依 admin、max、plus、free 順序處理賣單。"""
    buy_id = insert_buy_order(max_price=3000)
    admin_id = insert_sell_order(account_name="admin_seller", level="admin")
    max_id = insert_sell_order(account_name="max_seller", level="max")
    plus_id = insert_sell_order(account_name="plus_seller", level="plus")
    free_id = insert_sell_order(account_name="free_seller", level="free")

    result = orchestrator_server.process_batch(batch_size=4)

    assert result["processedCount"] == 4
    assert [item["sellOrderId"] for item in result["results"]] == [admin_id, max_id, plus_id, free_id]
    assert [item["buyOrderId"] for item in result["results"]] == [buy_id, buy_id, buy_id, buy_id]
    assert all(item["status"] == "candidate_found" for item in result["results"])


def test_same_level_sell_orders_use_queue_at_fifo() -> None:
    """測試同等級賣單依 queue_at 與 id 保持 FIFO。"""
    insert_buy_order(max_price=3000)
    older_id = insert_sell_order(account_name="older_max", level="max", queue_at=iso_now(-2))
    newer_id = insert_sell_order(account_name="newer_max", level="max", queue_at=iso_now(-1))

    result = orchestrator_server.process_batch(batch_size=2)

    assert [item["sellOrderId"] for item in result["results"]] == [older_id, newer_id]


def test_candidate_found_updates_notes_queue_at_and_keeps_amounts() -> None:
    """測試找到候選買單時只寫紀錄與回隊尾，不扣 remaining_amount。"""
    old_queue_at = iso_now(-1)
    buy_id = insert_buy_order(amount=10, max_price=3000)
    sell_id = insert_sell_order(amount=1, min_price=2900, queue_at=old_queue_at)

    result = orchestrator_server.process_batch(batch_size=1)

    assert result["results"][0]["status"] == "candidate_found"

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, attempts, queue_at, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert buy["remaining_amount"] == 10
    assert "candidate_for_sell_order" in buy["operation_note"]
    assert sell["remaining_amount"] == 1
    assert sell["attempts"] == 0
    assert sell["queue_at"] != old_queue_at
    assert "candidate_found" in sell["operation_note"]


def test_prepare_agent_task_returns_sell_order_and_candidate_buy_orders_without_changing_orders() -> None:
    """測試 prepare_agent_task 只整理資料給 agents，不修改訂單內容。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=10, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    task_result = orchestrator_server.prepare_agent_task(candidate_limit=3)

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, attempts, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert task_result["status"] == "prepared"
    assert task_result["task"]["sellOrder"]["id"] == sell_id
    assert [order["id"] for order in task_result["task"]["candidateBuyOrders"]] == [buy_id]
    assert task_result["task"]["acceptedDecisionStatuses"] == [
        "proposed_execution",
        "matched",
        "rejected",
        "invalid",
        "request_external_contract_data",
    ]
    decision = fetch_decision(task_result["task"]["taskId"])
    assert decision["sell_order_id"] == sell_id
    assert decision["candidate_buy_order_ids_json"] == f"[{buy_id}]"
    assert decision["decision_status"] == "prepared"
    assert decision["agent_decision_json"] is None
    assert decision["apply_result_json"] is None
    assert buy["remaining_amount"] == 10
    assert buy["status"] == "pending"
    assert buy["operation_note"] == ""
    assert sell["remaining_amount"] == 1
    assert sell["status"] == "pending"
    assert sell["attempts"] == 0
    assert sell["operation_note"] == ""


def test_proposed_execution_records_execution_without_changing_orders() -> None:
    """測試主腦回傳 proposed_execution 時，只記錄成交單，不改買賣單。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=10, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    task = orchestrator_server.prepare_agent_task(candidate_limit=3)["task"]
    result = orchestrator_server.apply_agent_decision(
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
            "agentNotes": "模擬 agent 判斷價格區間可接受",
        }
    )

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["decisionStatus"] == "proposed_execution"
    assert result["executionStatus"] == "proposed"
    decision = fetch_decision(task["taskId"])
    assert decision["decision_status"] == "proposed_execution"
    assert "filledAmount" in decision["agent_decision_json"]
    assert "executionPayload" in decision["apply_result_json"]
    assert decision["applied_at"] is not None
    assert buy["remaining_amount"] == 10
    assert buy["status"] == "pending"
    assert buy["operation_note"] == ""
    assert sell["remaining_amount"] == 1
    assert sell["status"] == "pending"
    assert sell["operation_note"] == ""

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        execution = conn.execute(
            "SELECT execution_id, status, proposal_json FROM executions WHERE execution_id = ?",
            (result["executionId"],),
        ).fetchone()
    assert execution is not None
    assert execution[1] == "proposed"


def test_proposed_execution_fills_payload_from_order_intents() -> None:
    """測試 Grok 只給 match 時，後端會從 DB 補齊鏈上 intent/signature。"""
    buy_id = insert_buy_order(
        account_name="buyer_a",
        amount=1.2,
        max_price=3100,
        intent_json=make_intent("0xBuyer", "3720000000", "1200000000000000000", "11"),
        signature="0xbuy_signature",
    )
    sell_id = insert_sell_order(
        account_name="seller_a",
        amount=2.6,
        min_price=3000,
        intent_json=make_intent("0xSeller", "2600000000000000000", "7800000000", "22"),
        signature="0xsell_signature",
    )

    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1.2,
                    "unitPriceUsdc": 3050,
                }
            ],
        }
    )

    payload = result["executionPayload"]

    assert result["executionStatus"] == "proposed"
    assert payload["intentA"]["intent"]["user"] == "0xSeller"
    assert payload["intentA"]["signature"] == "0xsell_signature"
    assert payload["executeAmountIn"] == "1200000000000000000"
    assert payload["routeDetails"]["matchedIntentB"]["intent"]["user"] == "0xBuyer"
    assert payload["routeDetails"]["matchedIntentB"]["signature"] == "0xbuy_signature"
    assert payload["routeDetails"]["matchedIntentB"]["executeAmountInB"] == "3720000000"


def test_multi_match_proposed_execution_is_split_into_multiple_executions() -> None:
    """測試多筆 matches 會拆成多筆 execution，符合單一 matchedIntentB 的鏈上格式。"""
    buy_a = insert_buy_order(
        account_name="buyer_a",
        amount=1,
        max_price=3100,
        intent_json=make_intent("0xBuyerA", "3100000000", "1000000000000000000", "33"),
        signature="0xbuy_a_signature",
    )
    buy_b = insert_buy_order(
        account_name="buyer_b",
        amount=1.5,
        max_price=3080,
        intent_json=make_intent("0xBuyerB", "4620000000", "1500000000000000000", "44"),
        signature="0xbuy_b_signature",
    )
    sell_id = insert_sell_order(
        account_name="seller_large",
        amount=2.5,
        min_price=3000,
        intent_json=make_intent("0xSeller", "2500000000000000000", "7500000000", "55"),
        signature="0xsell_signature",
    )

    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_a,
                    "filledAmount": 1,
                    "unitPriceUsdc": 3050,
                },
                {
                    "buyOrderId": buy_b,
                    "filledAmount": 1.5,
                    "unitPriceUsdc": 3060,
                },
            ],
        }
    )

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        rows = conn.execute("SELECT execution_payload_json FROM executions ORDER BY id ASC").fetchall()
    payloads = [json.loads(row[0]) for row in rows]

    assert result["executionStatus"] == "proposed"
    assert len(result["executionIds"]) == 2
    assert len(rows) == 2
    assert [payload["routeDetails"]["matchedIntentB"]["intent"]["user"] for payload in payloads] == [
        "0xBuyerA",
        "0xBuyerB",
    ]
    assert [payload["executeAmountIn"] for payload in payloads] == [
        "1000000000000000000",
        "1500000000000000000",
    ]
    assert [payload["routeDetails"]["matchedIntentB"]["executeAmountInB"] for payload in payloads] == [
        "3100000000",
        "4620000000",
    ]


def test_prepare_agent_task_skips_sell_order_with_open_execution() -> None:
    """測試已有 proposed execution 的賣單不會被重複派給主腦。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=10, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    task = orchestrator_server.prepare_agent_task(candidate_limit=3)["task"]
    orchestrator_server.apply_agent_decision(
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
        }
    )

    next_task = orchestrator_server.prepare_agent_task(candidate_limit=3)

    assert next_task["status"] == "no_pending_sell_order"
    assert next_task["task"] is None


def test_external_dex_proposed_execution_can_be_recorded_without_buy_matches() -> None:
    """測試外部 DEX actionType=0 成交提案不需要本地買單 matches。"""
    sell_id = insert_sell_order(account_name="seller_a", amount=100000000, min_price=0)

    result = orchestrator_server.apply_agent_decision(
        {
            "taskId": "task-external-dex",
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [],
            "executionPayload": {
                "intentA": {
                    "intent": {
                        "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                        "tokenIn": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
                        "tokenOut": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
                        "amountIn": "100000000",
                        "minAmountOut": "1",
                        "deadline": 1999999999,
                        "salt": "0x" + "11" * 32,
                        "allowPartialFill": True,
                    },
                    "signature": "0xsignature",
                },
                "actionType": 0,
                "executeAmountIn": "100000000",
                "routeDetails": {
                    "Calldata": "0x04e45aaf",
                    "matchedIntentB": None,
                    "treasuryAmountOut": None,
                },
            },
        }
    )

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["decisionStatus"] == "proposed_execution"
    assert result["matches"] == []
    assert result["executionPayload"]["actionType"] == 0
    assert sell["remaining_amount"] == 100000000
    assert sell["status"] == "pending"
    assert sell["operation_note"] == ""


def test_confirm_execution_updates_remaining_amounts_and_statuses() -> None:
    """測試 executor 確認成交後，中控才扣買賣單剩餘量與更新狀態。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=10, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    proposed = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1,
                    "unitPriceUsdc": 2950,
                }
            ],
            "agentNotes": "主腦提出成交單",
        }
    )
    result = orchestrator_server.confirm_execution(
        proposed["executionId"],
        {"status": "confirmed", "notes": "executor confirmed"},
    )

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["status"] == "execution_confirmed"
    assert result["applyResult"]["decisionStatus"] == "matched"
    assert result["applyResult"]["sellOrderStatus"] == "filled"
    assert buy["remaining_amount"] == 9
    assert buy["status"] == "pending"
    assert "agent_matched" in buy["operation_note"]
    assert sell["remaining_amount"] == 0
    assert sell["status"] == "filled"
    assert "agent_matched" in sell["operation_note"]

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        execution = conn.execute(
            "SELECT status, confirmation_json, apply_result_json FROM executions WHERE execution_id = ?",
            (proposed["executionId"],),
        ).fetchone()
    assert execution[0] == "confirmed"
    assert "confirmed" in execution[1]
    assert "totalFilledAmount" in execution[2]


def test_matched_decision_treats_float_dust_as_filled() -> None:
    """測試拆單浮點誤差不會讓實際已成交完的賣單殘留 pending。"""
    buy_id_1 = insert_buy_order(account_name="buyer_a", amount=1.4, max_price=3000)
    buy_id_2 = insert_buy_order(account_name="buyer_b", amount=1.1, max_price=3000)
    buy_id_3 = insert_buy_order(account_name="buyer_c", amount=0.9, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=2.6, min_price=2900)

    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "matched",
            "sellOrderId": sell_id,
            "matches": [
                {"buyOrderId": buy_id_1, "filledAmount": 1.4, "unitPriceUsdc": 2980},
                {"buyOrderId": buy_id_2, "filledAmount": 1.1, "unitPriceUsdc": 2980},
                {"buyOrderId": buy_id_3, "filledAmount": 0.1, "unitPriceUsdc": 2980},
            ],
        }
    )

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status FROM sell_orders WHERE id = ?",
        (sell_id,),
    )
    buy_3 = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status FROM buy_orders WHERE id = ?",
        (buy_id_3,),
    )

    assert result["sellRemainingAmount"] == 0
    assert result["sellOrderStatus"] == "filled"
    assert sell["remaining_amount"] == 0
    assert sell["status"] == "filled"
    assert buy_3["remaining_amount"] == 0.8
    assert buy_3["status"] == "pending"


def test_confirm_external_dex_execution_updates_only_sell_order() -> None:
    """測試外部 DEX confirmed 後，只扣賣單剩餘量，不需要本地買單。"""
    sell_id = insert_sell_order(account_name="seller_a", amount=100000000, min_price=0)

    proposed = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [],
            "executionPayload": {
                "intentA": {
                    "intent": {
                        "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                        "tokenIn": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
                        "tokenOut": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
                        "amountIn": "100000000",
                        "minAmountOut": "1",
                        "deadline": 1999999999,
                        "salt": "0x" + "22" * 32,
                        "allowPartialFill": True,
                    },
                    "signature": "0xsignature",
                },
                "actionType": 0,
                "executeAmountIn": "100000000",
                "routeDetails": {
                    "Calldata": "0x04e45aaf",
                    "matchedIntentB": None,
                    "treasuryAmountOut": None,
                },
            },
        }
    )

    result = orchestrator_server.confirm_execution(
        proposed["executionId"],
        {
            "status": "confirmed",
            "filledAmount": 50000000,
            "txHash": "0xabc",
            "notes": "executor confirmed external dex swap",
        },
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["status"] == "execution_confirmed"
    assert result["applyResult"]["executionType"] == "external_dex"
    assert result["applyResult"]["sellRemainingAmount"] == 50000000
    assert result["applyResult"]["buyOrders"] == []
    assert sell["remaining_amount"] == 50000000
    assert sell["status"] == "pending"
    assert "external_dex_matched" in sell["operation_note"]


def test_failed_execution_counts_attempt_without_deducting_orders() -> None:
    """測試 executor 回覆 failed 時不扣買賣單，但會讓賣單計一次失敗並回隊尾。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=10, max_price=3000)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    proposed = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "proposed_execution",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1,
                    "unitPriceUsdc": 2950,
                }
            ],
        }
    )
    result = orchestrator_server.confirm_execution(
        proposed["executionId"],
        {"status": "failed", "failureReason": "executor simulation failed"},
    )

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, attempts, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["status"] == "execution_failed"
    assert result["applyResult"]["status"] == "execution_failed_order_updated"
    assert buy["remaining_amount"] == 10
    assert buy["status"] == "pending"
    assert buy["operation_note"] == ""
    assert sell["remaining_amount"] == 1
    assert sell["status"] == "pending"
    assert sell["attempts"] == 1
    assert "execution_failed: reason=executor simulation failed" in sell["operation_note"]

    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        execution = conn.execute(
            "SELECT status, failure_reason FROM executions WHERE execution_id = ?",
            (proposed["executionId"],),
        ).fetchone()
    assert execution == ("failed", "executor simulation failed")


def test_apply_agent_matched_decision_returns_partially_filled_sell_order_to_tail() -> None:
    """測試 agent 只成交部分賣單時，賣單維持 pending 並更新 queue_at。"""
    buy_id = insert_buy_order(account_name="buyer_a", amount=1, max_price=3000)
    old_queue_at = iso_now(-1)
    sell_id = insert_sell_order(account_name="seller_a", amount=3, min_price=2900, queue_at=old_queue_at)

    result = orchestrator_server.apply_agent_decision(
        {
            "decisionStatus": "matched",
            "sellOrderId": sell_id,
            "matches": [
                {
                    "buyOrderId": buy_id,
                    "filledAmount": 1,
                    "unitPriceUsdc": 2950,
                }
            ],
        }
    )

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT remaining_amount, status FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT remaining_amount, status, queue_at FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["sellOrderStatus"] == "pending"
    assert buy["remaining_amount"] == 0
    assert buy["status"] == "filled"
    assert sell["remaining_amount"] == 2
    assert sell["status"] == "pending"
    assert sell["queue_at"] != old_queue_at


def test_apply_agent_rejected_decision_increments_attempts_and_returns_to_tail() -> None:
    """測試 agent 回傳 rejected 後，中控會讓賣單 attempts + 1 並回隊尾。"""
    old_queue_at = iso_now(-1)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900, queue_at=old_queue_at)

    task_id = "manual-rejected-task"
    result = orchestrator_server.apply_agent_decision(
        {
            "taskId": task_id,
            "decisionStatus": "rejected",
            "sellOrderId": sell_id,
            "failureReason": "模擬 agent 判斷目前不適合成交",
        }
    )

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, attempts, queue_at, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["decisionStatus"] == "rejected"
    assert result["sellOrderStatus"] == "pending"
    decision = fetch_decision(task_id)
    assert decision["decision_status"] == "rejected"
    assert decision["failure_reason"] == "模擬 agent 判斷目前不適合成交"
    assert sell["status"] == "pending"
    assert sell["attempts"] == 1
    assert sell["queue_at"] != old_queue_at
    assert "agent_rejected" in sell["operation_note"]


def test_apply_agent_invalid_decision_marks_sell_order_invalid() -> None:
    """測試 agent 回傳 invalid 後，中控會將賣單標記 invalid。"""
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900)

    task_id = "manual-invalid-task"
    result = orchestrator_server.apply_agent_decision(
        {
            "taskId": task_id,
            "decisionStatus": "invalid",
            "sellOrderId": sell_id,
            "failureReason": "模擬 agent 判斷資料不可執行",
        }
    )

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["decisionStatus"] == "invalid"
    assert result["sellOrderStatus"] == "invalid"
    decision = fetch_decision(task_id)
    assert decision["decision_status"] == "invalid"
    assert decision["failure_reason"] == "模擬 agent 判斷資料不可執行"
    assert sell["status"] == "invalid"
    assert "agent_invalid" in sell["operation_note"]


def test_apply_external_data_request_records_decision_without_changing_sell_order() -> None:
    """測試 agent 要求外部合約資料時，中控只記錄決策，不改賣單隊列。"""
    old_queue_at = iso_now(-1)
    sell_id = insert_sell_order(account_name="seller_a", amount=1, min_price=2900, queue_at=old_queue_at)
    task = orchestrator_server.prepare_agent_task(candidate_limit=3)["task"]

    result = orchestrator_server.apply_agent_decision(
        {
            "taskId": task["taskId"],
            "decisionStatus": "request_external_contract_data",
            "sellOrderId": sell_id,
            "reason": "本地候選不足，要求查外部合約",
            "externalQuery": {
                "sourceOrderType": "sell",
                "syncTargets": [],
            },
        }
    )

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, attempts, queue_at, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )
    decision = fetch_decision(task["taskId"])

    assert result["status"] == "external_contract_data_requested"
    assert result["decisionStatus"] == "request_external_contract_data"
    assert sell["status"] == "pending"
    assert sell["attempts"] == 0
    assert sell["queue_at"] == old_queue_at
    assert sell["operation_note"] == ""
    assert decision["decision_status"] == "request_external_contract_data"
    assert "externalQuery" in decision["agent_decision_json"]


def test_no_candidate_increments_attempts_and_returns_sell_order_to_tail() -> None:
    """測試找不到候選買單時，賣單 attempts + 1 並更新 queue_at 回隊尾。"""
    old_queue_at = iso_now(-1)
    sell_id = insert_sell_order(min_price=5000, queue_at=old_queue_at)

    result = orchestrator_server.process_batch(batch_size=1)

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, attempts, queue_at, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["results"][0]["status"] == "candidate_not_found"
    assert sell["status"] == "pending"
    assert sell["attempts"] == 1
    assert sell["queue_at"] != old_queue_at
    assert "candidate_not_found" in sell["operation_note"]


def test_no_candidate_marks_sell_order_invalid_at_max_attempts() -> None:
    """測試 attempts 達上限後，賣單會被標記 invalid。"""
    sell_id = insert_sell_order(min_price=5000, attempts=2)

    result = orchestrator_server.process_batch(batch_size=1)

    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, attempts, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    assert result["results"][0]["orderStatus"] == "invalid"
    assert sell["status"] == "invalid"
    assert sell["attempts"] == 3
    assert "標記 invalid" in sell["operation_note"]


def test_refresh_timeouts_archives_buy_and_sell_orders() -> None:
    """測試 timeout 掃描會歸檔超時買單與賣單，並更新原訂單狀態。"""
    old_time = iso_now(-20)
    buy_id = insert_buy_order(created_at=old_time)
    sell_id = insert_sell_order(created_at=old_time, queue_at=old_time)

    result = orchestrator_server.refresh_timeouts()

    buy = fetch_one(
        orchestrator_server.BUY_ORDERS_DB,
        "SELECT status, operation_note FROM buy_orders WHERE id = ?",
        (buy_id,),
    )
    sell = fetch_one(
        orchestrator_server.SELL_ORDERS_DB,
        "SELECT status, operation_note FROM sell_orders WHERE id = ?",
        (sell_id,),
    )

    with sqlite3.connect(orchestrator_server.TIMEOUT_ORDERS_DB) as conn:
        timeout_rows = conn.execute(
            "SELECT order_type, order_id, reason FROM timeout_orders ORDER BY id ASC"
        ).fetchall()

    assert result["buyTimedOut"] == 1
    assert result["sellTimedOut"] == 1
    assert buy["status"] == "timeout"
    assert sell["status"] == "timeout"
    assert "timeout" in buy["operation_note"]
    assert "timeout" in sell["operation_note"]
    assert timeout_rows == [("buy", buy_id, "timeout"), ("sell", sell_id, "timeout")]
