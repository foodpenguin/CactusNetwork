from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from scripts import execution_messages
from scripts import orchestrator_server


DEFAULT_RECONCILE_LIMIT = 20
DEFAULT_RECONCILE_INTERVAL_SECONDS = 30.0


def reconcile_keeperhub_executions(
    limit: int = DEFAULT_RECONCILE_LIMIT,
    dispatch_ready: bool = True,
    expire_invalid: bool = True,
    refresh_dispatched: bool = True,
    wait_for_final_result: bool = True,
    webhook_url: str | None = None,
    timeout_seconds: float = execution_messages.DEFAULT_KEEPERHUB_TIMEOUT_SECONDS,
    webhook_headers: dict[str, str] | None = None,
    poll_interval_seconds: float = 5.0,
    max_wait_seconds: float = 300.0,
    status_api_base: str | None = None,
    status_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    執行一次 execution 自動收尾巡檢。

    輸入：
    - `limit`：本次最多掃描幾筆 proposed / dispatched execution。
    - `dispatch_ready`：是否自動送出欄位完整且未過期的 proposed execution。
    - `expire_invalid`：是否把缺欄位、過期、或訂單已不可處理的 proposed execution 標成 failed。
    - `refresh_dispatched`：是否查詢已送出 KeeperHub 的 running execution。
    - `wait_for_final_result`：送出 KeeperHub 後是否等待 confirmed / failed。
    - `webhook_url`、`webhook_headers`：KeeperHub webhook 設定。
    - `timeout_seconds`：單次 HTTP request timeout 秒數。
    - `poll_interval_seconds`、`max_wait_seconds`：等待 KeeperHub 最終結果的輪詢設定。
    - `status_api_base`、`status_headers`：KeeperHub status API 設定。

    輸出：
    - 回傳本次巡檢摘要，包含刷新、過期清理、派送、略過與錯誤清單。

    副作用：
    - 可能將 proposed execution 送到 KeeperHub。
    - 可能將缺欄位、過期、或訂單已不可處理的 proposed execution 標成 failed。
    - KeeperHub 回 confirmed 時會正式扣買賣單；回 failed 時只釋放 execution 鎖。
    """
    safe_limit = max(1, int(limit))
    result: dict[str, Any] = {
        "status": "execution_reconcile_completed",
        "limit": safe_limit,
        "refreshBefore": None,
        "expired": [],
        "dispatched": [],
        "skipped": [],
        "errors": [],
        "refreshAfter": None,
    }

    if refresh_dispatched:
        result["refreshBefore"] = execution_messages.refresh_keeperhub_execution_results(
            limit=safe_limit,
            timeout_seconds=timeout_seconds,
            status_api_base=status_api_base,
            status_headers=status_headers,
        )

    proposed_requests = execution_messages.get_pending_execution_requests(limit=safe_limit, ready_only=False)
    for request in proposed_requests:
        execution_id = str(request["executionId"])
        rejection_reason = _proposed_rejection_reason(request)
        if rejection_reason:
            if not expire_invalid:
                result["skipped"].append(
                    {
                        "executionId": execution_id,
                        "reason": rejection_reason,
                        "action": "expire_invalid_disabled",
                    }
                )
                continue
            try:
                expired = _fail_execution(execution_id, rejection_reason)
                result["expired"].append(expired)
            except Exception as exc:  # pragma: no cover - defensive guard for live reconciliation
                result["errors"].append(
                    {
                        "executionId": execution_id,
                        "stage": "expire_invalid",
                        "error": str(exc),
                    }
                )
            continue

        if not request["readyForExecutor"]:
            result["skipped"].append(
                {
                    "executionId": execution_id,
                    "reason": "not_ready",
                    "missingFields": request.get("missingFields") or [],
                }
            )
            continue

        if not dispatch_ready:
            result["skipped"].append({"executionId": execution_id, "reason": "dispatch_ready_disabled"})
            continue

        preflight = execution_messages.check_execution_payload_onchain_preflight(request["payload"])
        if preflight["status"] == "failed":
            if not expire_invalid:
                result["skipped"].append(
                    {
                        "executionId": execution_id,
                        "reason": preflight["failureReason"],
                        "preflight": preflight,
                        "action": "onchain_preflight_failed_expire_disabled",
                    }
                )
                continue
            try:
                expired = _fail_execution(execution_id, f"鏈上預檢失敗：{preflight['failureReason']}")
                expired["preflight"] = preflight
                result["expired"].append(expired)
            except Exception as exc:  # pragma: no cover - defensive guard for live reconciliation
                result["errors"].append(
                    {
                        "executionId": execution_id,
                        "stage": "onchain_preflight_failed",
                        "preflight": preflight,
                        "error": str(exc),
                    }
                )
            continue
        if preflight["status"] == "error":
            result["errors"].append(
                {
                    "executionId": execution_id,
                    "stage": "onchain_preflight",
                    "error": preflight["failureReason"],
                    "preflight": preflight,
                }
            )
            continue

        try:
            dispatch_result = execution_messages.send_execution_to_keeperhub(
                execution_id,
                webhook_url=webhook_url,
                timeout_seconds=timeout_seconds,
                webhook_headers=webhook_headers or {},
                wait_for_final_result=wait_for_final_result,
                poll_interval_seconds=poll_interval_seconds,
                max_wait_seconds=max_wait_seconds,
                status_api_base=status_api_base,
                status_headers=status_headers or {},
                run_onchain_preflight=False,
            )
            dispatch_result["preflight"] = preflight
            result["dispatched"].append(dispatch_result)
        except Exception as exc:  # pragma: no cover - live network failures are captured, not fatal to whole batch
            result["errors"].append(
                {
                    "executionId": execution_id,
                    "stage": "dispatch_keeperhub",
                    "error": str(exc),
                }
            )

    if refresh_dispatched:
        result["refreshAfter"] = execution_messages.refresh_keeperhub_execution_results(
            limit=safe_limit,
            timeout_seconds=timeout_seconds,
            status_api_base=status_api_base,
            status_headers=status_headers,
        )

    result["summary"] = {
        "expiredCount": len(result["expired"]),
        "dispatchedCount": len(result["dispatched"]),
        "skippedCount": len(result["skipped"]),
        "errorCount": len(result["errors"]),
        "refreshBeforeFinalizedCount": _count_refresh_finalized(result["refreshBefore"]),
        "refreshAfterFinalizedCount": _count_refresh_finalized(result["refreshAfter"]),
    }
    return result


def run_reconcile_loop(
    interval_seconds: float = DEFAULT_RECONCILE_INTERVAL_SECONDS,
    **kwargs: Any,
) -> None:
    """
    持續執行 execution 自動收尾巡檢。

    輸入：
    - `interval_seconds`：每輪巡檢間隔。
    - `kwargs`：傳給 `reconcile_keeperhub_executions()` 的參數。

    輸出：
    - 無回傳；每輪結果以 JSON line 印到 stdout，方便 systemd / VM log 收集。

    副作用：
    - 持續掃描並收尾 executions。
    """
    safe_interval = max(1.0, float(interval_seconds))
    while True:
        result = reconcile_keeperhub_executions(**kwargs)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(safe_interval)


def _proposed_rejection_reason(request: dict[str, Any]) -> str | None:
    """
    判斷 proposed execution 是否應該直接失敗釋放鎖。

    輸入：
    - `request`：`execution_messages.get_pending_execution_requests()` 回傳的單筆資料。

    輸出：
    - 可送出時回傳 `None`。
    - 不可送出時回傳失敗原因。
    """
    missing_fields = request.get("missingFields") or []
    if missing_fields:
        return f"execution payload 缺少必要欄位：{', '.join(str(field) for field in missing_fields)}"

    payload = request.get("payload") or {}
    expired_reason = _deadline_expired_reason(payload)
    if expired_reason:
        return expired_reason

    order_reason = _linked_order_rejection_reason(request)
    if order_reason:
        return order_reason

    return None


def _deadline_expired_reason(payload: dict[str, Any]) -> str | None:
    """
    檢查 intent deadline 是否已過期。

    輸入：
    - `payload`：嚴格 execution payload。

    輸出：
    - 未過期回傳 `None`；任一 intent 過期時回傳原因。
    """
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    deadline_sources: list[tuple[str, Any]] = [
        ("intentA.intent.deadline", ((payload.get("intentA") or {}).get("intent") or {}).get("deadline")),
    ]
    matched_intent_b = ((payload.get("routeDetails") or {}).get("matchedIntentB") or {})
    if isinstance(matched_intent_b, dict):
        deadline_sources.append(
            (
                "routeDetails.matchedIntentB.intent.deadline",
                ((matched_intent_b.get("intent") or {})).get("deadline"),
            )
        )

    for field_name, raw_deadline in deadline_sources:
        try:
            deadline = int(raw_deadline)
        except (TypeError, ValueError):
            return f"{field_name} 不是有效的 Unix timestamp"
        if deadline <= now_epoch:
            return f"{field_name} 已過期，deadline={deadline}，now={now_epoch}"
    return None


def _linked_order_rejection_reason(request: dict[str, Any]) -> str | None:
    """
    檢查 execution 對應的買賣單是否仍可被處理。

    輸入：
    - `request`：單筆 execution request。

    輸出：
    - 訂單仍可處理時回傳 `None`。
    - 任一對應訂單已非 pending 或剩餘量無效時回傳原因。
    """
    sell_order_id = request.get("sellOrderId")
    sell = _fetch_order(orchestrator_server.SELL_ORDERS_DB, "sell_orders", sell_order_id)
    if sell is None:
        return f"sell_order_id={sell_order_id} 不存在"
    if sell["status"] != "pending":
        return f"sell_order_id={sell_order_id} 狀態為 {sell['status']}，不再可處理"
    if float(sell["remaining_amount"]) <= 0:
        return f"sell_order_id={sell_order_id} remaining_amount <= 0"

    proposal = request.get("proposal") or {}
    for match in proposal.get("matches") or []:
        buy_order_id = match.get("buyOrderId")
        buy = _fetch_order(orchestrator_server.BUY_ORDERS_DB, "buy_orders", buy_order_id)
        if buy is None:
            return f"buy_order_id={buy_order_id} 不存在"
        if buy["status"] != "pending":
            return f"buy_order_id={buy_order_id} 狀態為 {buy['status']}，不再可處理"
        if float(buy["remaining_amount"]) <= 0:
            return f"buy_order_id={buy_order_id} remaining_amount <= 0"
    return None


def _fetch_order(db_path: Any, table_name: str, order_id: Any) -> sqlite3.Row | None:
    """
    讀取買單或賣單。

    輸入：
    - `db_path`：資料庫路徑。
    - `table_name`：`buy_orders` 或 `sell_orders`。
    - `order_id`：訂單 id。

    輸出：
    - 找到時回傳 `sqlite3.Row`，否則回傳 `None`。
    """
    if order_id in (None, ""):
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(f"SELECT * FROM {table_name} WHERE id = ?", (order_id,)).fetchone()


def _fail_execution(execution_id: str, failure_reason: str) -> dict[str, Any]:
    """
    將 execution 標記為 failed。

    輸入：
    - `execution_id`：本地 execution id。
    - `failure_reason`：失敗原因。

    輸出：
    - 回傳 `submit_execution_result()` 結果。
    """
    return execution_messages.submit_execution_result(
        execution_id,
        {
            "status": "failed",
            "failure_reason": failure_reason,
            "notes": "execution reconciler marked this request failed before dispatch",
        },
    )


def _count_refresh_finalized(refresh_result: Any) -> int:
    """
    計算 KeeperHub refresh 結果中的 finalized 筆數。

    輸入：
    - `refresh_result`：refresh 回傳資料或 `None`。

    輸出：
    - finalized 數量。
    """
    if not isinstance(refresh_result, dict):
        return 0
    finalized = refresh_result.get("finalized")
    if not isinstance(finalized, list):
        return 0
    return len(finalized)


def run_cli() -> None:
    """
    execution 自動收尾器 CLI。

    輸入：
    - `once`：執行一次巡檢。
    - `loop`：持續巡檢。

    輸出：
    - `once` 輸出單次 JSON。
    - `loop` 每輪輸出一行 JSON。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork execution reconciler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(target: argparse.ArgumentParser) -> None:
        target.add_argument("--limit", type=int, default=DEFAULT_RECONCILE_LIMIT)
        target.add_argument("--no-dispatch-ready", action="store_true")
        target.add_argument("--no-expire-invalid", action="store_true")
        target.add_argument("--no-refresh-dispatched", action="store_true")
        target.add_argument("--no-wait-for-final-result", action="store_true")
        target.add_argument("--webhook-url")
        target.add_argument("--timeout-seconds", type=float, default=execution_messages.DEFAULT_KEEPERHUB_TIMEOUT_SECONDS)
        target.add_argument("--poll-interval-seconds", type=float, default=5.0)
        target.add_argument("--max-wait-seconds", type=float, default=300.0)
        target.add_argument("--status-api-base")

    once_parser = subparsers.add_parser("once")
    add_common_arguments(once_parser)

    loop_parser = subparsers.add_parser("loop")
    add_common_arguments(loop_parser)
    loop_parser.add_argument("--interval-seconds", type=float, default=DEFAULT_RECONCILE_INTERVAL_SECONDS)

    args = parser.parse_args()
    kwargs = {
        "limit": args.limit,
        "dispatch_ready": not args.no_dispatch_ready,
        "expire_invalid": not args.no_expire_invalid,
        "refresh_dispatched": not args.no_refresh_dispatched,
        "wait_for_final_result": not args.no_wait_for_final_result,
        "webhook_url": args.webhook_url,
        "timeout_seconds": args.timeout_seconds,
        "poll_interval_seconds": args.poll_interval_seconds,
        "max_wait_seconds": args.max_wait_seconds,
        "status_api_base": args.status_api_base,
    }
    if args.command == "once":
        output = reconcile_keeperhub_executions(**kwargs)
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.command == "loop":
        run_reconcile_loop(interval_seconds=args.interval_seconds, **kwargs)
        return
    raise ValueError(f"未知 command：{args.command}")


if __name__ == "__main__":
    run_cli()
