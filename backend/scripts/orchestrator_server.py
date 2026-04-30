from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "databases"
BUY_ORDERS_DB = DATA_DIR / "buy_orders.db"
SELL_ORDERS_DB = DATA_DIR / "sell_orders.db"
ORCHESTRATOR_STATE_DB = DATA_DIR / "orchestrator_state.db"
TIMEOUT_ORDERS_DB = DATA_DIR / "timeout_orders.db"
DECISIONS_DB = DATA_DIR / "decisions.db"
EXECUTIONS_DB = DATA_DIR / "executions.db"

REFRESH_INTERVAL_SECONDS = 15 * 60
ORDER_TIMEOUT_MINUTES = int(os.getenv("ORCHESTRATOR_ORDER_TIMEOUT_MINUTES", "15"))
MAX_ATTEMPTS = int(os.getenv("ORCHESTRATOR_MAX_ATTEMPTS", "3"))
NON_ADMIN_WEIGHT_SEQUENCE = ["max", "max", "max", "max", "max", "plus", "plus", "free"]


def _now() -> datetime:
    """
    取得目前 UTC 時間。

    輸入：
    - 無。

    輸出：
    - 回傳 timezone-aware 的 `datetime`。

    用途：
    - 統一所有 DB 時間比較與 `operation_note` 時間戳。
    """
    return datetime.now(timezone.utc)


def _append_note(old_note: str | None, message: str) -> str:
    """
    將一筆中控操作紀錄追加到 `operation_note`。

    輸入：
    - `old_note`：訂單目前既有的操作紀錄，可為空字串或 `None`。
    - `message`：這次要追加的人類可讀操作摘要。

    輸出：
    - 回傳新的完整 `operation_note` 字串。

    副作用：
    - 無；此函式只組字串，不直接寫 DB。
    """
    line = f"[{_now().isoformat()}] {message}"
    if old_note:
        return f"{old_note}\n{line}"
    return line


def _ensure_databases() -> None:
    """
    確保中控工具需要的 DB 與欄位都存在。

    輸入：
    - 無；固定檢查 `buy_orders.db`、`sell_orders.db`、`orchestrator_state.db`、`timeout_orders.db`、`decisions.db`、`executions.db`。

    輸出：
    - 無回傳值。

    副作用：
    - 建立 `data/databases/` 資料夾。
    - 替買單/賣單表補上 `operation_note` 欄位。
    - 替賣單表補上 `queue_at` 欄位，舊資料會用 `created_at` 補值。
    - 建立 `orchestrator_state.db` 的 `state` 表與 `weighted_index` 初始值。
    - 建立 `timeout_orders.db` 的 `timeout_orders` 表。
    - 建立 `decisions.db` 的 `decisions` 表。
    - 建立 `executions.db` 的 `executions` 表。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for db_path, table_name in [(BUY_ORDERS_DB, "buy_orders"), (SELL_ORDERS_DB, "sell_orders")]:
        with sqlite3.connect(db_path) as conn:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")]
            if "operation_note" not in columns:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN operation_note TEXT NOT NULL DEFAULT ''")
            if table_name == "sell_orders" and "queue_at" not in columns:
                conn.execute("ALTER TABLE sell_orders ADD COLUMN queue_at TEXT")
                conn.execute("UPDATE sell_orders SET queue_at = created_at WHERE queue_at IS NULL")
            if "intent_json" not in columns:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN intent_json TEXT")
            if "signature" not in columns:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN signature TEXT")
            conn.commit()

    with sqlite3.connect(ORCHESTRATOR_STATE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO state (key, value) VALUES ('weighted_index', '0')"
        )
        conn.commit()

    with sqlite3.connect(TIMEOUT_ORDERS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS timeout_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_type TEXT NOT NULL,
                order_id INTEGER NOT NULL,
                account_name TEXT NOT NULL,
                account_level_snapshot TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount REAL NOT NULL,
                remaining_amount REAL NOT NULL,
                status_before_timeout TEXT NOT NULL,
                reason TEXT NOT NULL,
                original_order_json TEXT NOT NULL,
                timed_out_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    with sqlite3.connect(DECISIONS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                sell_order_id INTEGER NOT NULL,
                candidate_buy_order_ids_json TEXT NOT NULL,
                task_json TEXT NOT NULL,
                decision_status TEXT NOT NULL,
                agent_decision_json TEXT,
                apply_result_json TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                applied_at TEXT
            )
            """
        )
        conn.commit()

    with sqlite3.connect(EXECUTIONS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL UNIQUE,
                task_id TEXT,
                sell_order_id INTEGER NOT NULL,
                proposal_json TEXT NOT NULL,
                execution_payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                confirmation_json TEXT,
                apply_result_json TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed_at TEXT
            )
            """
        )
        conn.commit()


def refresh_timeouts() -> dict[str, Any]:
    """
    掃描 pending 訂單並處理 timeout。

    輸入：
    - 無直接參數。
    - timeout 分鐘數由環境變數 `ORCHESTRATOR_ORDER_TIMEOUT_MINUTES` 控制，預設 15 分鐘。

    輸出：
    - 回傳 dict：
      - `timeoutMinutes`：本次使用的 timeout 分鐘數。
      - `buyTimedOut`：本次被標記 timeout 的買單數。
      - `sellTimedOut`：本次被標記 timeout 的賣單數。

    副作用：
    - 將超時 pending 訂單寫入 `timeout_orders.db`。
    - 將原始買單或賣單狀態改成 `timeout`。
    - 在原始訂單 `operation_note` 追加 timeout 紀錄。
    """
    _ensure_databases()
    cutoff = _now() - timedelta(minutes=ORDER_TIMEOUT_MINUTES)
    summary: dict[str, Any] = {"timeoutMinutes": ORDER_TIMEOUT_MINUTES, "buyTimedOut": 0, "sellTimedOut": 0}

    for order_type, db_path, table_name in [
        ("buy", BUY_ORDERS_DB, "buy_orders"),
        ("sell", SELL_ORDERS_DB, "sell_orders"),
    ]:
        timeout_time_expr = "COALESCE(queue_at, created_at)" if table_name == "sell_orders" else "created_at"
        with sqlite3.connect(db_path) as source_conn:
            source_conn.row_factory = sqlite3.Row
            rows = source_conn.execute(
                f"""
                SELECT *
                FROM {table_name}
                WHERE status = 'pending' AND datetime({timeout_time_expr}) <= datetime(?)
                ORDER BY datetime({timeout_time_expr}) ASC, id ASC
                """,
                (cutoff.isoformat(),),
            ).fetchall()

            for row in rows:
                row_dict = dict(row)
                note = _append_note(row_dict.get("operation_note"), f"timeout: 超過 {ORDER_TIMEOUT_MINUTES} 分鐘未完成，移入 timeout_orders.db")
                timed_out_at = _now().isoformat()

                with sqlite3.connect(TIMEOUT_ORDERS_DB) as timeout_conn:
                    timeout_conn.execute(
                        """
                        INSERT INTO timeout_orders (
                            order_type,
                            order_id,
                            account_name,
                            account_level_snapshot,
                            asset,
                            amount,
                            remaining_amount,
                            status_before_timeout,
                            reason,
                            original_order_json,
                            timed_out_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            order_type,
                            row_dict["id"],
                            row_dict["account_name"],
                            row_dict["account_level_snapshot"],
                            row_dict["asset"],
                            row_dict["amount"],
                            row_dict["remaining_amount"],
                            row_dict["status"],
                            "timeout",
                            json.dumps(row_dict, ensure_ascii=False),
                            timed_out_at,
                        ),
                    )
                    timeout_conn.commit()

                source_conn.execute(
                    f"""
                    UPDATE {table_name}
                    SET status = 'timeout', operation_note = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (note, timed_out_at, row_dict["id"]),
                )
                if order_type == "buy":
                    summary["buyTimedOut"] += 1
                else:
                    summary["sellTimedOut"] += 1

            source_conn.commit()

    return summary


def process_batch(batch_size: int = 8) -> dict[str, Any]:
    """
    批次處理賣單佇列，替每筆賣單尋找候選買單。

    輸入：
    - `batch_size`：本次最多處理幾筆 pending 賣單。

    輸出：
    - 回傳 dict：
      - `message`：批次處理結果文字。
      - `refresh`：本次處理前 timeout 掃描結果。
      - `processedCount`：實際處理的賣單筆數。
      - `results`：每筆賣單的候選搜尋結果。

    排序規則：
    - admin 賣單永遠優先。
    - 非 admin 依 `max, max, max, max, max, plus, plus, free` 權重輪詢。
    - 同等級依 `queue_at ASC, id ASC`。

    副作用：
    - 會先呼叫 `refresh_timeouts()`。
    - 找到候選買單時，會更新買單與賣單的 `operation_note`。
    - 找不到候選買單時，會更新賣單 `attempts`、`queue_at` 與 `operation_note`。
    - 目前不會真的成交，也不會扣 `remaining_amount`。
    """
    _ensure_databases()
    refresh_result = refresh_timeouts()
    processed_sell_ids: set[int] = set()
    results: list[dict[str, Any]] = []

    for _ in range(batch_size):
        sell_order = _select_next_sell_order(processed_sell_ids)
        if sell_order is None:
            break

        processed_sell_ids.add(sell_order["id"])
        candidate = _find_candidate_buy_order(sell_order)
        if candidate is None:
            result = _mark_sell_order_no_candidate(sell_order)
        else:
            result = _mark_candidate_found(candidate, sell_order)
        results.append(result)

    return {
        "message": "批次處理完成",
        "refresh": refresh_result,
        "processedCount": len(results),
        "results": results,
    }


def get_queue_status() -> dict[str, Any]:
    """
    讀取目前中控佇列總覽。

    輸入：
    - 無。

    輸出：
    - 回傳 dict：
      - `sellQueues`：各帳號等級 pending 賣單數量，包含 `admin/max/plus/free`。
      - `buyPending`：目前 pending 買單總數。
      - `timeoutOrders`：timeout 歸檔資料庫內的總筆數。

    副作用：
    - 會呼叫 `_ensure_databases()` 確保資料庫可讀。
    - 不會更改訂單狀態。
    """
    _ensure_databases()
    status: dict[str, Any] = {
        "sellQueues": {"admin": 0, "max": 0, "plus": 0, "free": 0},
        "buyPending": 0,
        "timeoutOrders": 0,
    }

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        for level, count in conn.execute(
            """
            SELECT account_level_snapshot, COUNT(*)
            FROM sell_orders
            WHERE status = 'pending'
            GROUP BY account_level_snapshot
            """
        ):
            status["sellQueues"][level] = count

    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        status["buyPending"] = conn.execute(
            "SELECT COUNT(*) FROM buy_orders WHERE status = 'pending'"
        ).fetchone()[0]

    with sqlite3.connect(TIMEOUT_ORDERS_DB) as conn:
        status["timeoutOrders"] = conn.execute("SELECT COUNT(*) FROM timeout_orders").fetchone()[0]

    return status


def prepare_agent_task(candidate_limit: int = 5) -> dict[str, Any]:
    """
    準備一份要交給 agents 判斷的中控任務。

    輸入：
    - `candidate_limit`：最多帶給 agents 的候選買單數量。

    輸出：
    - 若沒有 pending 賣單，回傳：
      - `status = "no_pending_sell_order"`
      - `task = None`
    - 若有任務，回傳：
      - `status = "prepared"`
      - `task`：包含 `taskId`、賣單資料、候選買單列表與基本撮合規則。

    副作用：
    - 會先呼叫 `refresh_timeouts()`。
    - 會依目前賣單佇列挑選下一筆賣單。
    - 非 admin 賣單被選中時，會推進 `weighted_index`，視為已派發一個 agent 任務。
    - 不會修改任何買單/賣單的 `remaining_amount`、`status`、`queue_at` 或 `operation_note`。
    """
    _ensure_databases()
    refresh_result = refresh_timeouts()
    sell_order = _select_next_sell_order(set())
    prepared_at = _now().isoformat()

    if sell_order is None:
        return {
            "status": "no_pending_sell_order",
            "preparedAt": prepared_at,
            "refresh": refresh_result,
            "task": None,
        }

    candidate_buy_orders = _find_candidate_buy_orders(sell_order, candidate_limit)
    sell_order_id = int(sell_order["id"])
    task_id = f"sell:{sell_order_id}:{prepared_at}"
    task = {
        "taskId": task_id,
        "sellOrder": _sell_order_to_agent_payload(sell_order),
        "candidateBuyOrders": [_buy_order_to_agent_payload(row) for row in candidate_buy_orders],
        "matchingRule": {
            "assetMustMatch": True,
            "buyMaxUnitPriceMustCoverSellMinUnitPrice": True,
            "note": "買單 asset == 賣單 asset 且買單 max_unit_price_usdc >= 賣單 min_unit_price_usdc",
        },
        "acceptedDecisionStatuses": [
            "proposed_execution",
            "matched",
            "rejected",
            "invalid",
            "request_external_contract_data",
        ],
    }
    _record_prepared_decision(task, prepared_at)
    return {
        "status": "prepared",
        "preparedAt": prepared_at,
        "refresh": refresh_result,
        "task": task,
    }


def apply_agent_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """
    套用 agents 回覆的決策，並更新本地買單/賣單隊列。

    輸入：
    - `decision`：agent 回傳的 dict。
      - `decisionStatus` 必填，可為 `proposed_execution`、`matched`、`rejected`、`invalid`、`request_external_contract_data`。
      - `sellOrderId` 必填，表示這次決策針對哪一筆賣單。
      - `agentNotes` 選填，會寫入 `operation_note`。
      - `failureReason` 選填，通常用於 `rejected` 或 `invalid`。
      - `matches` 在內部 OTC `proposed_execution` 或 `matched` 時必填，每筆包含：
        - `buyOrderId`
        - `filledAmount`
        - `unitPriceUsdc`
      - 外部 DEX `proposed_execution` 使用 `executionPayload.actionType = 0` 與 `routeDetails.Calldata`，可不含本地買單 `matches`。

    輸出：
    - 回傳 dict，描述本次套用後的訂單狀態與剩餘量。

    副作用：
    - `proposed_execution`：只記錄成交提案到 `executions.db`，不改買賣單。
    - `matched`：代表外部執行已確認，依確認內容扣買單/賣單 `remaining_amount`，必要時改成 `filled`。
    - `rejected`：賣單 `attempts + 1`，未達上限時回隊尾，達上限時改成 `invalid`。
    - `invalid`：賣單直接改成 `invalid`。
    - 所有決策都會寫入相關訂單的 `operation_note`。
    """
    status = str(decision.get("decisionStatus", "")).strip()
    if status == "proposed_execution":
        result = record_execution_proposal(decision)
        _record_applied_decision(decision, result)
        return result
    if status == "matched":
        result = _apply_matched_decision(decision)
        _record_applied_decision(decision, result)
        return result
    if status == "rejected":
        result = _apply_rejected_decision(decision)
        _record_applied_decision(decision, result)
        return result
    if status == "invalid":
        result = _apply_invalid_decision(decision)
        _record_applied_decision(decision, result)
        return result
    if status == "request_external_contract_data":
        result = _apply_external_data_request_decision(decision)
        _record_applied_decision(decision, result)
        return result
    raise ValueError("decisionStatus 必須是 proposed_execution、matched、rejected、invalid 或 request_external_contract_data")


def record_execution_proposal(decision: dict[str, Any]) -> dict[str, Any]:
    """
    記錄主腦提出的成交單，但不更新買單/賣單成交狀態。

    輸入：
    - `decision`：主腦回傳的成交提案 dict。
      - `decisionStatus` 應為 `proposed_execution`。
      - `sellOrderId` 必填。
      - 內部 OTC `matches` 必填，格式與 confirmed 後的 `matched` 相同。
      - 外部 DEX `actionType=0` 可使用空 `matches`，但必須有 `routeDetails.Calldata`。
      - `executionPayload` 選填，用於未來 executor / keeper。

    輸出：
    - 回傳成交提案紀錄摘要，包含 `executionId` 與 `executionStatus = proposed`。

    副作用：
    - 寫入 `executions.db`。
    - 不修改 `buy_orders.db`。
    - 不修改 `sell_orders.db`。
    """
    _ensure_databases()
    sell_order_id = int(decision["sellOrderId"])
    if _is_external_dex_execution_decision(decision):
        _validate_external_dex_execution(decision)
    else:
        _validate_execution_matches(decision)

    created_at = _now().isoformat()
    task_id = decision.get("taskId")
    execution_id = str(decision.get("executionId") or f"execution:{sell_order_id}:{created_at}")
    execution_payload = decision.get("executionPayload") or {
        "type": "local_order_match",
        "sellOrderId": sell_order_id,
        "matches": decision.get("matches", []),
    }

    with sqlite3.connect(EXECUTIONS_DB) as conn:
        conn.execute(
            """
            INSERT INTO executions (
                execution_id,
                task_id,
                sell_order_id,
                proposal_json,
                execution_payload_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) DO UPDATE SET
                task_id = excluded.task_id,
                sell_order_id = excluded.sell_order_id,
                proposal_json = excluded.proposal_json,
                execution_payload_json = excluded.execution_payload_json,
                status = excluded.status,
                updated_at = excluded.updated_at,
                confirmation_json = NULL,
                apply_result_json = NULL,
                failure_reason = NULL,
                confirmed_at = NULL
            """,
            (
                execution_id,
                task_id,
                sell_order_id,
                json.dumps(decision, ensure_ascii=False, sort_keys=True),
                json.dumps(execution_payload, ensure_ascii=False, sort_keys=True),
                "proposed",
                created_at,
                created_at,
            ),
        )
        conn.commit()

    return {
        "status": "execution_proposed",
        "decisionStatus": "proposed_execution",
        "executionId": execution_id,
        "executionStatus": "proposed",
        "sellOrderId": sell_order_id,
        "matches": decision.get("matches", []),
        "executionPayload": execution_payload,
    }


def confirm_execution(execution_id: str, confirmation: dict[str, Any]) -> dict[str, Any]:
    """
    接收 executor / keeper 回覆，確認後才更新買賣單成交狀態。

    輸入：
    - `execution_id`：`record_execution_proposal()` 建立的成交單 id。
    - `confirmation`：執行器回覆 dict。
      - `status = confirmed` 時，會套用成交結果。
      - `status = failed` 時，只標記成交單失敗，不改訂單。

    輸出：
    - 回傳 execution 更新摘要與必要時的訂單套用結果。

    副作用：
    - `confirmed`：呼叫內部 matched 套用流程，才扣除 `remaining_amount` 並更新訂單狀態。
    - `failed`：只更新 `executions.db`，不修改買賣單。
    """
    _ensure_databases()
    confirmed_at = _now().isoformat()
    confirmation_status = str(confirmation.get("status") or "").strip() or "confirmed"

    with sqlite3.connect(EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        execution = conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    if execution is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    if execution["status"] not in ("proposed", "dispatched"):
        raise ValueError(f"execution_id={execution_id} 目前狀態不是 proposed 或 dispatched")

    if confirmation_status == "failed":
        failure_reason = str(confirmation.get("failureReason") or "execution failed")
        with sqlite3.connect(EXECUTIONS_DB) as conn:
            conn.execute(
                """
                UPDATE executions
                SET status = 'failed',
                    confirmation_json = ?,
                    failure_reason = ?,
                    updated_at = ?,
                    confirmed_at = ?
                WHERE execution_id = ?
                """,
                (
                    json.dumps(confirmation, ensure_ascii=False, sort_keys=True),
                    failure_reason,
                    confirmed_at,
                    confirmed_at,
                    execution_id,
                ),
            )
            conn.commit()
        return {
            "status": "execution_failed",
            "executionId": execution_id,
            "executionStatus": "failed",
            "failureReason": failure_reason,
            "applyResult": None,
        }

    if confirmation_status != "confirmed":
        raise ValueError("confirmation status 必須是 confirmed 或 failed")

    proposal = json.loads(execution["proposal_json"])
    if _is_external_dex_execution_decision(proposal):
        apply_result = _apply_external_dex_confirmation(proposal, confirmation)
    else:
        matched_decision = {
            **proposal,
            "decisionStatus": "matched",
            "agentNotes": str(confirmation.get("notes") or proposal.get("agentNotes") or "executor confirmed execution"),
        }
        apply_result = _apply_matched_decision(matched_decision)

    with sqlite3.connect(EXECUTIONS_DB) as conn:
        conn.execute(
            """
            UPDATE executions
            SET status = 'confirmed',
                confirmation_json = ?,
                apply_result_json = ?,
                updated_at = ?,
                confirmed_at = ?
            WHERE execution_id = ?
            """,
            (
                json.dumps(confirmation, ensure_ascii=False, sort_keys=True),
                json.dumps(apply_result, ensure_ascii=False, sort_keys=True),
                confirmed_at,
                confirmed_at,
                execution_id,
            ),
        )
        conn.commit()

    return {
        "status": "execution_confirmed",
        "executionId": execution_id,
        "executionStatus": "confirmed",
        "applyResult": apply_result,
    }


def _select_next_sell_order(excluded_ids: set[int]) -> sqlite3.Row | None:
    """
    挑選本輪下一筆要處理的 pending 賣單。

    輸入：
    - `excluded_ids`：本次 batch 已經處理過的賣單 id，避免同一輪重複處理。

    輸出：
    - 找到時回傳一筆 `sqlite3.Row` 賣單資料。
    - 找不到 pending 賣單時回傳 `None`。

    排序規則：
    - admin 賣單永遠優先，依 `queue_at ASC, id ASC`。
    - 非 admin 依 `orchestrator_state.db` 的 `weighted_index` 對應權重序列。

    副作用：
    - 若選到非 admin 賣單，會推進 `weighted_index`。
    """
    excluded_sql = ""
    params: list[Any] = []
    if excluded_ids:
        placeholders = ",".join("?" for _ in excluded_ids)
        excluded_sql = f"AND id NOT IN ({placeholders})"
        params.extend(sorted(excluded_ids))

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        admin = conn.execute(
            f"""
            SELECT *
            FROM sell_orders
            WHERE status = 'pending' AND account_level_snapshot = 'admin' {excluded_sql}
            ORDER BY datetime(queue_at) ASC, id ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if admin is not None:
            return admin

        with sqlite3.connect(ORCHESTRATOR_STATE_DB) as state_conn:
            index = int(state_conn.execute("SELECT value FROM state WHERE key = 'weighted_index'").fetchone()[0])
            for offset in range(len(NON_ADMIN_WEIGHT_SEQUENCE)):
                next_index = (index + offset) % len(NON_ADMIN_WEIGHT_SEQUENCE)
                level = NON_ADMIN_WEIGHT_SEQUENCE[next_index]
                row = conn.execute(
                    f"""
                    SELECT *
                    FROM sell_orders
                    WHERE status = 'pending' AND account_level_snapshot = ? {excluded_sql}
                    ORDER BY datetime(queue_at) ASC, id ASC
                    LIMIT 1
                    """,
                    [level, *params],
                ).fetchone()
                if row is not None:
                    state_conn.execute(
                        "UPDATE state SET value = ? WHERE key = 'weighted_index'",
                        (str((next_index + 1) % len(NON_ADMIN_WEIGHT_SEQUENCE)),),
                    )
                    state_conn.commit()
                    return row

    return None


def _find_candidate_buy_order(sell_order: sqlite3.Row) -> sqlite3.Row | None:
    """
    根據賣單尋找一筆候選買單。

    輸入：
    - `sell_order`：目前被中控選中的 pending 賣單。

    輸出：
    - 找到時回傳一筆 `sqlite3.Row` 買單資料。
    - 找不到符合條件的 pending 買單時回傳 `None`。

    基本規則：
    - 買單 `asset` 必須等於賣單 `asset`。
    - 買單 `max_unit_price_usdc` 必須大於或等於賣單 `min_unit_price_usdc`。

    排序規則：
    - 目前優先選最高買價，再依建立時間與 id 排序。
    """
    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT *
            FROM buy_orders
            WHERE status = 'pending'
              AND remaining_amount > 0
              AND asset = ?
              AND max_unit_price_usdc >= ?
            ORDER BY max_unit_price_usdc DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (sell_order["asset"], sell_order["min_unit_price_usdc"]),
        ).fetchone()


def _find_candidate_buy_orders(sell_order: sqlite3.Row, limit: int) -> list[sqlite3.Row]:
    """
    根據賣單尋找多筆候選買單，供 agents 做外部決策。

    輸入：
    - `sell_order`：目前派給 agents 的賣單。
    - `limit`：最多回傳幾筆候選買單。

    輸出：
    - 回傳 `sqlite3.Row` list。
    - 若沒有符合條件的買單，回傳空 list。

    副作用：
    - 無；此函式只讀取 `buy_orders.db`。
    """
    safe_limit = max(1, int(limit))
    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT *
            FROM buy_orders
            WHERE status = 'pending'
              AND remaining_amount > 0
              AND asset = ?
              AND max_unit_price_usdc >= ?
            ORDER BY max_unit_price_usdc DESC, created_at ASC, id ASC
            LIMIT ?
            """,
            (sell_order["asset"], sell_order["min_unit_price_usdc"], safe_limit),
        ).fetchall()


def _record_prepared_decision(task: dict[str, Any], created_at: str) -> None:
    """
    將 prepare 階段交給 agents 的 task 快照寫入 `decisions.db`。

    輸入：
    - `task`：`prepare_agent_task()` 產生的 task dict。
    - `created_at`：task 建立時間。

    輸出：
    - 無回傳值。

    副作用：
    - 在 `decisions` 表新增或覆蓋一筆 `decision_status = prepared` 的紀錄。
    """
    candidate_ids = [order["id"] for order in task["candidateBuyOrders"]]
    with sqlite3.connect(DECISIONS_DB) as conn:
        conn.execute(
            """
            INSERT INTO decisions (
                task_id,
                sell_order_id,
                candidate_buy_order_ids_json,
                task_json,
                decision_status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                sell_order_id = excluded.sell_order_id,
                candidate_buy_order_ids_json = excluded.candidate_buy_order_ids_json,
                task_json = excluded.task_json,
                decision_status = excluded.decision_status,
                created_at = excluded.created_at,
                agent_decision_json = NULL,
                apply_result_json = NULL,
                failure_reason = NULL,
                applied_at = NULL
            """
            ,
            (
                task["taskId"],
                task["sellOrder"]["id"],
                json.dumps(candidate_ids, ensure_ascii=False),
                json.dumps(task, ensure_ascii=False),
                "prepared",
                created_at,
            ),
        )
        conn.commit()


def _record_applied_decision(decision: dict[str, Any], apply_result: dict[str, Any]) -> None:
    """
    將 agents 回覆與中控套用結果寫入 `decisions.db`。

    輸入：
    - `decision`：agents 回傳的決策 dict。
    - `apply_result`：`apply_agent_decision()` 套用後的結果 dict。

    輸出：
    - 無回傳值。

    副作用：
    - 若 `taskId` 已存在，更新同一筆 decisions 紀錄。
    - 若 `taskId` 不存在，建立一筆最小紀錄，避免決策遺失。
    """
    task_id = str(decision.get("taskId") or f"manual:{decision.get('sellOrderId')}:{_now().isoformat()}")
    sell_order_id = int(decision["sellOrderId"])
    applied_at = _now().isoformat()
    decision_status = str(decision.get("decisionStatus", ""))
    failure_reason = decision.get("failureReason")

    with sqlite3.connect(DECISIONS_DB) as conn:
        existing = conn.execute("SELECT id FROM decisions WHERE task_id = ?", (task_id,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO decisions (
                    task_id,
                    sell_order_id,
                    candidate_buy_order_ids_json,
                    task_json,
                    decision_status,
                    agent_decision_json,
                    apply_result_json,
                    failure_reason,
                    created_at,
                    applied_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    sell_order_id,
                    "[]",
                    "{}",
                    decision_status,
                    json.dumps(decision, ensure_ascii=False),
                    json.dumps(apply_result, ensure_ascii=False),
                    failure_reason,
                    applied_at,
                    applied_at,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE decisions
                SET decision_status = ?,
                    agent_decision_json = ?,
                    apply_result_json = ?,
                    failure_reason = ?,
                    applied_at = ?
                WHERE task_id = ?
                """,
                (
                    decision_status,
                    json.dumps(decision, ensure_ascii=False),
                    json.dumps(apply_result, ensure_ascii=False),
                    failure_reason,
                    applied_at,
                    task_id,
                ),
            )
        conn.commit()


def _buy_order_to_agent_payload(row: sqlite3.Row) -> dict[str, Any]:
    """
    將買單資料列轉成 agents 使用的 JSON-friendly dict。

    輸入：
    - `row`：`buy_orders` 的 SQLite Row。

    輸出：
    - 回傳欄位名稱較適合 JSON 的買單 dict。

    副作用：
    - 無；只做資料格式轉換。
    """
    return {
        "id": row["id"],
        "accountName": row["account_name"],
        "accountLevelSnapshot": row["account_level_snapshot"],
        "asset": row["asset"],
        "amount": row["amount"],
        "remainingAmount": row["remaining_amount"],
        "maxUnitPriceUsdc": row["max_unit_price_usdc"],
        "maxSplits": row["max_splits"],
        "maxFeePercent": row["max_fee_percent"],
        "status": row["status"],
        "attempts": row["attempts"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "operationNote": row["operation_note"],
        "intentJson": _parse_json_or_none(row["intent_json"]),
        "signature": row["signature"],
        "hasIntent": row["intent_json"] is not None,
        "hasSignature": row["signature"] is not None,
    }


def _sell_order_to_agent_payload(row: sqlite3.Row) -> dict[str, Any]:
    """
    將賣單資料列轉成 agents 使用的 JSON-friendly dict。

    輸入：
    - `row`：`sell_orders` 的 SQLite Row。

    輸出：
    - 回傳欄位名稱較適合 JSON 的賣單 dict。

    副作用：
    - 無；只做資料格式轉換。
    """
    return {
        "id": row["id"],
        "accountName": row["account_name"],
        "accountLevelSnapshot": row["account_level_snapshot"],
        "asset": row["asset"],
        "amount": row["amount"],
        "remainingAmount": row["remaining_amount"],
        "minUnitPriceUsdc": row["min_unit_price_usdc"],
        "maxSplits": row["max_splits"],
        "maxFeePercent": row["max_fee_percent"],
        "status": row["status"],
        "attempts": row["attempts"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "queueAt": row["queue_at"],
        "operationNote": row["operation_note"],
        "intentJson": _parse_json_or_none(row["intent_json"]),
        "signature": row["signature"],
        "hasIntent": row["intent_json"] is not None,
        "hasSignature": row["signature"] is not None,
    }


def _parse_json_or_none(raw_value: str | None) -> Any:
    """
    將 DB 內的 JSON 文字轉回 Python 物件。

    輸入：
    - `raw_value`：SQLite 文字欄位，可為 `None`。

    輸出：
    - 有值且 JSON 格式正確時回傳解析後物件。
    - `None` 或空字串回傳 `None`。

    錯誤：
    - JSON 格式錯誤時回傳 `None`，避免壞資料阻斷中控流程。
    """
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return None


def _load_order_or_raise(db_path: Path, table_name: str, order_id: int) -> sqlite3.Row:
    """
    依 id 讀取單筆訂單，不存在時直接拋出錯誤。

    輸入：
    - `db_path`：訂單所在 SQLite DB。
    - `table_name`：`buy_orders` 或 `sell_orders`。
    - `order_id`：訂單 id。

    輸出：
    - 回傳 `sqlite3.Row`。

    錯誤：
    - 找不到訂單時拋出 `ValueError`。
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT * FROM {table_name} WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise ValueError(f"{table_name} id={order_id} 不存在")
    return row


def _validate_execution_matches(decision: dict[str, Any]) -> None:
    """
    驗證成交提案內容是否符合目前本地訂單約束。

    輸入：
    - `decision`：包含 `sellOrderId` 與 `matches` 的成交提案。

    輸出：
    - 無回傳值。

    錯誤：
    - 若買賣單不存在、狀態不是 pending、資產不一致、價格不符或數量超過剩餘量，拋出 `ValueError`。

    副作用：
    - 無；此函式只驗證，不修改 DB。
    """
    sell_order_id = int(decision["sellOrderId"])
    matches = decision.get("matches") or []
    if not matches:
        raise ValueError("成交提案必須包含 matches")

    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    sell_remaining = float(sell_order["remaining_amount"])
    total_filled = 0.0
    for match in matches:
        buy_order_id = int(match["buyOrderId"])
        filled_amount = float(match["filledAmount"])
        unit_price = float(match["unitPriceUsdc"])
        if filled_amount <= 0:
            raise ValueError("filledAmount 必須大於 0")

        buy_order = _load_order_or_raise(BUY_ORDERS_DB, "buy_orders", buy_order_id)
        if buy_order["status"] != "pending":
            raise ValueError(f"buy_order_id={buy_order_id} 不是 pending 狀態")
        if buy_order["asset"] != sell_order["asset"]:
            raise ValueError("買單與賣單 asset 不一致")
        if float(buy_order["max_unit_price_usdc"]) < unit_price:
            raise ValueError("unitPriceUsdc 超過買單最高接受價格")
        if float(sell_order["min_unit_price_usdc"]) > unit_price:
            raise ValueError("unitPriceUsdc 低於賣單最低接受價格")
        if filled_amount > float(buy_order["remaining_amount"]):
            raise ValueError("filledAmount 超過買單 remaining_amount")
        total_filled += filled_amount
        if total_filled > sell_remaining:
            raise ValueError("matches 總 filledAmount 超過賣單 remaining_amount")


def _is_external_dex_execution_decision(decision: dict[str, Any]) -> bool:
    """
    判斷成交提案是否為外部 DEX router.call(calldata) 類型。

    輸入：
    - `decision`：主腦回傳或 executions.db 裡的 proposal dict。

    輸出：
    - `executionPayload.actionType == 0` 且 `routeDetails.Calldata` 存在時回傳 `True`。
    """
    payload = decision.get("executionPayload") if isinstance(decision.get("executionPayload"), dict) else {}
    route_details = payload.get("routeDetails") if isinstance(payload.get("routeDetails"), dict) else {}
    return payload.get("actionType") == 0 and bool(route_details.get("Calldata"))


def _validate_external_dex_execution(decision: dict[str, Any]) -> None:
    """
    驗證外部 DEX 成交提案是否可被記錄。

    輸入：
    - `decision`：主腦回傳的 `proposed_execution`。

    輸出：
    - 無回傳值。

    錯誤：
    - 賣單不存在、賣單非 pending、payload 缺 `executeAmountIn` 或 `Calldata` 時拋出 `ValueError`。
    """
    sell_order_id = int(decision["sellOrderId"])
    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    payload = decision.get("executionPayload") or {}
    route_details = payload.get("routeDetails") or {}
    calldata = route_details.get("Calldata")
    if payload.get("actionType") != 0:
        raise ValueError("外部 DEX 成交提案 actionType 必須是 0")
    if payload.get("executeAmountIn") in (None, ""):
        raise ValueError("外部 DEX 成交提案缺 executeAmountIn")
    if not isinstance(calldata, str) or not calldata.startswith("0x"):
        raise ValueError("外部 DEX 成交提案缺 routeDetails.Calldata")


def _apply_matched_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """
    套用 agent 的 matched 決策。

    輸入：
    - `decision`：包含 `sellOrderId` 與 `matches` 的 agent 決策。

    輸出：
    - 回傳本次成交總量、賣單更新後狀態、買單更新後狀態列表。

    副作用：
    - 依 `filledAmount` 扣除買單與賣單 `remaining_amount`。
    - 剩餘量為 0 的訂單改成 `filled`，仍有剩餘的訂單維持 `pending`。
    - 賣單仍有剩餘時更新 `queue_at`，代表回隊尾。
    - 寫入買單與賣單 `operation_note`。
    """
    sell_order_id = int(decision["sellOrderId"])
    matches = decision.get("matches") or []
    if not matches:
        raise ValueError("matched 決策必須包含 matches")

    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    now = _now().isoformat()
    agent_notes = str(decision.get("agentNotes") or "").strip()
    sell_remaining = float(sell_order["remaining_amount"])
    total_filled = 0.0
    buy_updates: list[dict[str, Any]] = []

    normalized_matches: list[dict[str, Any]] = []
    for match in matches:
        buy_order_id = int(match["buyOrderId"])
        filled_amount = float(match["filledAmount"])
        unit_price = float(match["unitPriceUsdc"])
        if filled_amount <= 0:
            raise ValueError("filledAmount 必須大於 0")

        buy_order = _load_order_or_raise(BUY_ORDERS_DB, "buy_orders", buy_order_id)
        if buy_order["status"] != "pending":
            raise ValueError(f"buy_order_id={buy_order_id} 不是 pending 狀態")
        if buy_order["asset"] != sell_order["asset"]:
            raise ValueError("買單與賣單 asset 不一致")
        if float(buy_order["max_unit_price_usdc"]) < unit_price:
            raise ValueError("unitPriceUsdc 超過買單最高接受價格")
        if float(sell_order["min_unit_price_usdc"]) > unit_price:
            raise ValueError("unitPriceUsdc 低於賣單最低接受價格")
        if filled_amount > float(buy_order["remaining_amount"]):
            raise ValueError("filledAmount 超過買單 remaining_amount")
        total_filled += filled_amount
        if total_filled > sell_remaining:
            raise ValueError("matches 總 filledAmount 超過賣單 remaining_amount")

        normalized_matches.append(
            {
                "buyOrder": buy_order,
                "buyOrderId": buy_order_id,
                "filledAmount": filled_amount,
                "unitPriceUsdc": unit_price,
            }
        )

    for match in normalized_matches:
        buy_order = match["buyOrder"]
        new_buy_remaining = float(buy_order["remaining_amount"]) - match["filledAmount"]
        buy_status = "filled" if new_buy_remaining <= 0 else "pending"
        buy_note = _append_note(
            buy_order["operation_note"],
            (
                f"agent_matched: sell_order_id={sell_order_id}，"
                f"filled_amount={match['filledAmount']}，unit_price_usdc={match['unitPriceUsdc']}"
                + (f"，notes={agent_notes}" if agent_notes else "")
            ),
        )
        with sqlite3.connect(BUY_ORDERS_DB) as conn:
            conn.execute(
                """
                UPDATE buy_orders
                SET remaining_amount = ?, status = ?, operation_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (max(new_buy_remaining, 0), buy_status, buy_note, now, match["buyOrderId"]),
            )
            conn.commit()
        buy_updates.append(
            {
                "buyOrderId": match["buyOrderId"],
                "filledAmount": match["filledAmount"],
                "remainingAmount": max(new_buy_remaining, 0),
                "status": buy_status,
                "unitPriceUsdc": match["unitPriceUsdc"],
            }
        )

    new_sell_remaining = sell_remaining - total_filled
    sell_status = "filled" if new_sell_remaining <= 0 else "pending"
    sell_note = _append_note(
        sell_order["operation_note"],
        (
            f"agent_matched: total_filled_amount={total_filled}，"
            f"matches={json.dumps([{k: v for k, v in item.items() if k != 'buyOrder'} for item in normalized_matches], ensure_ascii=False)}"
            + (f"，notes={agent_notes}" if agent_notes else "")
        ),
    )
    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.execute(
            """
            UPDATE sell_orders
            SET remaining_amount = ?, status = ?, operation_note = ?, updated_at = ?, queue_at = ?
            WHERE id = ?
            """,
            (max(new_sell_remaining, 0), sell_status, sell_note, now, now, sell_order_id),
        )
        conn.commit()

    return {
        "status": "decision_applied",
        "decisionStatus": "matched",
        "sellOrderId": sell_order_id,
        "sellRemainingAmount": max(new_sell_remaining, 0),
        "sellOrderStatus": sell_status,
        "totalFilledAmount": total_filled,
        "buyOrders": buy_updates,
    }


def _apply_external_dex_confirmation(proposal: dict[str, Any], confirmation: dict[str, Any]) -> dict[str, Any]:
    """
    套用外部 DEX execution confirmed 結果。

    輸入：
    - `proposal`：先前記錄於 executions.db 的 actionType=0 成交提案。
    - `confirmation`：executor / keeper 回覆。
      - 建議帶 `filledAmount`，代表本地賣單數量單位的實際成交量。
      - 若未帶 `filledAmount`，會用 payload 的 `executeAmountIn`，並以賣單剩餘量作上限。

    輸出：
    - 回傳賣單剩餘量與狀態。

    副作用：
    - 只更新賣單；外部 DEX 沒有本地買單需要扣量。
    - 賣單仍有剩餘時更新 `queue_at`，回到隊尾等待下一輪。
    - 寫入 `operation_note`。
    """
    sell_order_id = int(proposal["sellOrderId"])
    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    payload = proposal.get("executionPayload") or {}
    raw_filled_amount = confirmation.get("filledAmount", confirmation.get("executeAmountIn", payload.get("executeAmountIn")))
    filled_amount = float(raw_filled_amount)
    if filled_amount <= 0:
        raise ValueError("外部 DEX confirmed filledAmount 必須大於 0")

    sell_remaining = float(sell_order["remaining_amount"])
    applied_filled_amount = min(filled_amount, sell_remaining)
    new_sell_remaining = sell_remaining - applied_filled_amount
    sell_status = "filled" if new_sell_remaining <= 0 else "pending"
    now = _now().isoformat()
    agent_notes = str(confirmation.get("notes") or proposal.get("agentNotes") or "external dex execution confirmed").strip()
    tx_hash = confirmation.get("txHash") or confirmation.get("transactionHash")
    note = _append_note(
        sell_order["operation_note"],
        (
            f"external_dex_matched: filled_amount={applied_filled_amount}"
            + (f"，tx_hash={tx_hash}" if tx_hash else "")
            + (f"，notes={agent_notes}" if agent_notes else "")
        ),
    )

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.execute(
            """
            UPDATE sell_orders
            SET remaining_amount = ?, status = ?, operation_note = ?, updated_at = ?, queue_at = ?
            WHERE id = ?
            """,
            (max(new_sell_remaining, 0), sell_status, note, now, now, sell_order_id),
        )
        conn.commit()

    return {
        "status": "decision_applied",
        "decisionStatus": "matched",
        "executionType": "external_dex",
        "sellOrderId": sell_order_id,
        "sellRemainingAmount": max(new_sell_remaining, 0),
        "sellOrderStatus": sell_status,
        "totalFilledAmount": applied_filled_amount,
        "buyOrders": [],
    }


def _apply_rejected_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """
    套用 agent 的 rejected 決策。

    輸入：
    - `decision`：至少包含 `sellOrderId`，可包含 `failureReason` 與 `agentNotes`。

    輸出：
    - 回傳賣單更新後的 attempts 與狀態。

    副作用：
    - 賣單 `attempts + 1`。
    - 未達 `MAX_ATTEMPTS` 時維持 `pending` 並更新 `queue_at` 回隊尾。
    - 達上限時標記 `invalid`。
    - 寫入 `operation_note`。
    """
    sell_order_id = int(decision["sellOrderId"])
    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    attempts = int(sell_order["attempts"]) + 1
    new_status = "invalid" if attempts >= MAX_ATTEMPTS else "pending"
    now = _now().isoformat()
    reason = str(decision.get("failureReason") or "agent rejected").strip()
    agent_notes = str(decision.get("agentNotes") or "").strip()
    note = _append_note(
        sell_order["operation_note"],
        (
            f"agent_rejected: reason={reason}，attempts={attempts}"
            + ("，已達上限，標記 invalid" if new_status == "invalid" else "，賣單回到隊尾")
            + (f"，notes={agent_notes}" if agent_notes else "")
        ),
    )

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.execute(
            """
            UPDATE sell_orders
            SET attempts = ?, status = ?, operation_note = ?, updated_at = ?, queue_at = ?
            WHERE id = ?
            """,
            (attempts, new_status, note, now, now, sell_order_id),
        )
        conn.commit()

    return {
        "status": "decision_applied",
        "decisionStatus": "rejected",
        "sellOrderId": sell_order_id,
        "sellOrderStatus": new_status,
        "attempts": attempts,
    }


def _apply_invalid_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """
    套用 agent 的 invalid 決策。

    輸入：
    - `decision`：至少包含 `sellOrderId`，可包含 `failureReason` 與 `agentNotes`。

    輸出：
    - 回傳賣單已被標記 invalid 的結果。

    副作用：
    - 將賣單狀態改成 `invalid`。
    - 寫入 `operation_note`。
    """
    sell_order_id = int(decision["sellOrderId"])
    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] not in {"pending", "invalid"}:
        raise ValueError(f"sell_order_id={sell_order_id} 目前狀態不可標記 invalid")

    now = _now().isoformat()
    reason = str(decision.get("failureReason") or "agent marked invalid").strip()
    agent_notes = str(decision.get("agentNotes") or "").strip()
    note = _append_note(
        sell_order["operation_note"],
        f"agent_invalid: reason={reason}" + (f"，notes={agent_notes}" if agent_notes else ""),
    )

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.execute(
            """
            UPDATE sell_orders
            SET status = 'invalid', operation_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (note, now, sell_order_id),
        )
        conn.commit()

    return {
        "status": "decision_applied",
        "decisionStatus": "invalid",
        "sellOrderId": sell_order_id,
        "sellOrderStatus": "invalid",
    }


def _apply_external_data_request_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """
    記錄 agent 要求外部合約資料的決策，但不改變訂單隊列。

    輸入：
    - `decision`：至少包含 `sellOrderId`，可包含：
      - `taskId`
      - `reason`
      - `externalQuery`

    輸出：
    - 回傳 dict，表示中控已接受這次外部資料請求。

    副作用：
    - 只寫入 `decisions.db`。
    - 不修改買單/賣單 `status`、`remaining_amount`、`operation_note` 或 `queue_at`。
    """
    sell_order_id = int(decision["sellOrderId"])
    sell_order = _load_order_or_raise(SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell_order["status"] != "pending":
        raise ValueError(f"sell_order_id={sell_order_id} 不是 pending 狀態")

    external_query = decision.get("externalQuery") or {}
    if not isinstance(external_query, dict):
        raise ValueError("externalQuery 必須是 object")

    return {
        "status": "external_contract_data_requested",
        "decisionStatus": "request_external_contract_data",
        "sellOrderId": sell_order_id,
        "sellOrderStatus": sell_order["status"],
        "externalQuery": external_query,
        "reason": decision.get("reason") or decision.get("failureReason"),
    }


def _mark_sell_order_no_candidate(sell_order: sqlite3.Row) -> dict[str, Any]:
    """
    處理賣單找不到候選買單的情況。

    輸入：
    - `sell_order`：本次找不到候選買單的賣單資料。

    輸出：
    - 回傳 dict：
      - `sellOrderId`：賣單 id。
      - `status`：固定為 `candidate_not_found`。
      - `orderStatus`：更新後的訂單狀態，可能是 `pending` 或 `invalid`。
      - `attempts`：更新後的嘗試次數。

    副作用：
    - 賣單 `attempts + 1`。
    - 未達上限時維持 `pending` 並更新 `queue_at`，代表回到隊尾。
    - 達到 `MAX_ATTEMPTS` 時改成 `invalid`。
    - 將本次失敗原因寫入 `operation_note`。
    """
    attempts = int(sell_order["attempts"]) + 1
    new_status = "invalid" if attempts >= MAX_ATTEMPTS else "pending"
    note_message = (
        f"candidate_not_found: 第 {attempts} 次找不到符合 asset={sell_order['asset']}、"
        f"min_unit_price_usdc={sell_order['min_unit_price_usdc']} 的買單"
    )
    if new_status == "invalid":
        note_message += f"，已達 MAX_ATTEMPTS={MAX_ATTEMPTS}，標記 invalid"
    else:
        note_message += "，賣單回到隊尾"
    note = _append_note(sell_order["operation_note"], note_message)
    now = _now().isoformat()

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.execute(
            """
            UPDATE sell_orders
            SET attempts = ?, status = ?, operation_note = ?, updated_at = ?, queue_at = ?
            WHERE id = ?
            """,
            (attempts, new_status, note, now, now, sell_order["id"]),
        )
        conn.commit()

    return {
        "sellOrderId": sell_order["id"],
        "status": "candidate_not_found",
        "orderStatus": new_status,
        "attempts": attempts,
    }


def _mark_candidate_found(buy_order: sqlite3.Row, sell_order: sqlite3.Row) -> dict[str, Any]:
    """
    處理賣單找到候選買單的情況。

    輸入：
    - `buy_order`：符合條件的候選買單。
    - `sell_order`：本次被處理的賣單。

    輸出：
    - 回傳 dict：
      - `buyOrderId`：候選買單 id。
      - `sellOrderId`：本次賣單 id。
      - `status`：固定為 `candidate_found`。
      - `asset`：撮合候選資產。
      - `buyMaxUnitPriceUsdc`：買方最高接受單價。
      - `sellMinUnitPriceUsdc`：賣方最低接受單價。

    副作用：
    - 在買單 `operation_note` 記錄它被哪張賣單選為候選。
    - 在賣單 `operation_note` 記錄找到哪張買單。
    - 更新賣單 `queue_at`，代表本階段尚未成交、回到隊尾等待後續決策。
    - 目前不會扣任何 `remaining_amount`，也不會改成 filled。
    """
    now = _now().isoformat()
    buy_note = _append_note(
        buy_order["operation_note"],
        f"candidate_for_sell_order: 被 sell_order_id={sell_order['id']} 選為候選，尚未成交",
    )
    sell_note = _append_note(
        sell_order["operation_note"],
        f"candidate_found: 找到 buy_order_id={buy_order['id']}，尚未成交，賣單回到隊尾等待後續 agent 決策",
    )

    with sqlite3.connect(BUY_ORDERS_DB) as buy_conn:
        buy_conn.execute(
            "UPDATE buy_orders SET operation_note = ?, updated_at = ? WHERE id = ?",
            (buy_note, now, buy_order["id"]),
        )
        buy_conn.commit()

    with sqlite3.connect(SELL_ORDERS_DB) as sell_conn:
        sell_conn.execute(
            "UPDATE sell_orders SET operation_note = ?, updated_at = ?, queue_at = ? WHERE id = ?",
            (sell_note, now, now, sell_order["id"]),
        )
        sell_conn.commit()

    return {
        "buyOrderId": buy_order["id"],
        "sellOrderId": sell_order["id"],
        "status": "candidate_found",
        "asset": buy_order["asset"],
        "buyMaxUnitPriceUsdc": buy_order["max_unit_price_usdc"],
        "sellMinUnitPriceUsdc": sell_order["min_unit_price_usdc"],
    }


def run_cli() -> None:
    """
    本地命令列入口。

    輸入：
    - CLI 第一個參數 `command`：
      - `status`：輸出目前佇列總覽。
      - `refresh`：掃描 timeout 並輸出本次歸檔數量。
      - `process-batch`：批次處理賣單佇列。
    - 可選參數 `--batch-size`：`process-batch` 最多處理幾筆賣單。

    輸出：
    - 將 JSON 結果印到 stdout。

    副作用：
    - 依 command 不同，可能更新 timeout、attempts、queue_at 與 operation_note。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork 本地 DB 中控檢查工具")
    parser.add_argument(
        "command",
        choices=["status", "refresh", "process-batch"],
        help="status=查看 DB 狀態；refresh=歸檔 timeout；process-batch=批次處理候選撮合",
    )
    parser.add_argument("--batch-size", type=int, default=8, help="process-batch 每次最多處理幾筆賣單")
    args = parser.parse_args()

    if args.command == "status":
        result = get_queue_status()
    elif args.command == "refresh":
        result = refresh_timeouts()
    else:
        result = process_batch(args.batch_size)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_cli()
