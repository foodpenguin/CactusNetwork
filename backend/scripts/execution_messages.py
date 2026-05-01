from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import orchestrator_server


FINAL_EXECUTION_STATUSES = {"confirmed", "failed"}
REQUIRED_INTENT_FIELDS = ("user", "tokenIn", "tokenOut", "amountIn", "minAmountOut", "deadline", "salt", "allowPartialFill")
PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
KEEPERHUB_WEBHOOK_URL_ENV = "KEEPERHUB_WEBHOOK_URL"
DEFAULT_KEEPERHUB_WEBHOOK_URL = "https://app.keeperhub.com/api/workflows/o2o3h3yf8s6ps4ogg8h81/webhook"
KEEPERHUB_STATUS_API_BASE_ENV = "KEEPERHUB_STATUS_API_BASE"
DEFAULT_KEEPERHUB_STATUS_API_BASE = "https://app.keeperhub.com/api/workflows/executions"
DEFAULT_KEEPERHUB_TIMEOUT_SECONDS = 60.0
KEEPERHUB_WAITING_STATUSES = {"pending", "running", "queued", "waiting"}
KEEPERHUB_SUCCESS_STATUSES = {"success", "succeeded", "completed", "complete", "confirmed"}
KEEPERHUB_FAILED_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}


def get_pending_execution_requests(limit: int = 20, ready_only: bool = False) -> list[dict[str, Any]]:
    """
    取得等待送給區塊鏈端的 execution requests。

    輸入：
    - `limit`：最多回傳幾筆。
    - `ready_only`：若為 `True`，只回傳 payload 欄位完整、可送給區塊鏈端的資料。

    輸出：
    - 回傳 execution request list。

    副作用：
    - 無；此函式只讀取 `executions.db`。
    """
    orchestrator_server._ensure_databases()
    safe_limit = max(1, int(limit))
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM executions
            WHERE status = 'proposed'
            ORDER BY datetime(created_at) ASC, id ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    requests = [_execution_row_to_request(row) for row in rows]
    if ready_only:
        return [request for request in requests if request["readyForExecutor"]]
    return requests


def get_execution_request(execution_id: str) -> dict[str, Any]:
    """
    取得單一 execution request。

    輸入：
    - `execution_id`：execution id。

    輸出：
    - 回傳區塊鏈端可讀的 execution request，其中 `payload` 完全遵照鏈上格式。
    """
    row = _fetch_execution_row(execution_id)
    if row is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    return _execution_row_to_request(row)


def mark_execution_dispatched(execution_id: str, dispatch_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    標記 execution request 已交給區塊鏈端。

    輸入：
    - `execution_id`：execution id。
    - `dispatch_metadata`：可選發送資訊，例如目標服務、request id。

    輸出：
    - 回傳 dispatched 摘要。

    副作用：
    - 將 `executions.status` 從 `proposed` 改成 `dispatched`。
    - 不更新買賣單。
    """
    orchestrator_server._ensure_databases()
    row = _fetch_execution_row(execution_id)
    if row is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    if row["status"] == "dispatched":
        return {
            "status": "already_dispatched",
            "executionId": execution_id,
            "executionStatus": "dispatched",
        }
    if row["status"] != "proposed":
        raise ValueError(f"execution_id={execution_id} 目前狀態不是 proposed")

    now = _now()
    confirmation = {
        "status": "dispatched",
        "dispatchMetadata": dispatch_metadata or {},
        "dispatchedAt": now,
    }
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        conn.execute(
            """
            UPDATE executions
            SET status = 'dispatched',
                confirmation_json = ?,
                updated_at = ?
            WHERE execution_id = ? AND status = 'proposed'
            """,
            (
                json.dumps(confirmation, ensure_ascii=False, sort_keys=True),
                now,
                execution_id,
            ),
        )
        conn.commit()

    return {
        "status": "execution_dispatched",
        "executionId": execution_id,
        "executionStatus": "dispatched",
        "dispatchMetadata": dispatch_metadata or {},
    }


def submit_execution_result(execution_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    接收區塊鏈端回報的 execution 結果。

    輸入：
    - `execution_id`：execution id。
    - `result`：區塊鏈端回報。
      - 成功：`{"status": "confirmed", "tx_hash": "...", "block_number": 123, "raw_receipt": {...}}`
      - 失敗：`{"status": "failed", "failure_reason": "..."}`

    輸出：
    - 回傳中控確認結果。

    副作用：
    - `confirmed` 時會呼叫 `orchestrator_server.confirm_execution()`，此時才更新買賣單。
    - `failed` 時只更新 execution，不改買賣單。
    - 若 execution 已是 confirmed/failed，回傳既有紀錄，不重複扣單。
    """
    row = _fetch_execution_row(execution_id)
    if row is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    if row["status"] in FINAL_EXECUTION_STATUSES:
        return {
            "status": "already_finalized",
            "executionId": execution_id,
            "executionStatus": row["status"],
            "executionRequest": _execution_row_to_request(row),
        }

    normalized = _normalize_execution_result(result)
    confirm_result = orchestrator_server.confirm_execution(execution_id, normalized)
    return {
        "status": "execution_result_accepted",
        "executionId": execution_id,
        "executionStatus": confirm_result["executionStatus"],
        "confirmResult": confirm_result,
    }


def send_execution_to_keeperhub(
    execution_id: str,
    webhook_url: str | None = None,
    timeout_seconds: float = DEFAULT_KEEPERHUB_TIMEOUT_SECONDS,
    webhook_headers: dict[str, str] | None = None,
    wait_for_final_result: bool = False,
    poll_interval_seconds: float = 5.0,
    max_wait_seconds: float = 300.0,
    status_api_base: str | None = None,
    status_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    將一筆 ready execution payload 送到 KeeperHub webhook，並回收 webhook 結果。

    輸入：
    - `execution_id`：要送出的 execution id。
    - `webhook_url`：KeeperHub webhook URL；未提供時讀取 `.env` 的 `KEEPERHUB_WEBHOOK_URL`，再退回預設 URL。
    - `timeout_seconds`：HTTP POST 等待秒數。
    - `webhook_headers`：額外 HTTP headers；用於 KeeperHub 需要授權時。
    - `wait_for_final_result`：若為 `True`，KeeperHub 回覆 running 後會持續查 status API，直到成功、失敗或逾時。
    - `poll_interval_seconds`：等待最終結果時，每次 status API 查詢間隔。
    - `max_wait_seconds`：等待最終結果的最長秒數。
    - `status_api_base`：可選 KeeperHub status API base URL。
    - `status_headers`：可選 KeeperHub status API 額外 headers。

    輸出：
    - 回傳 KeeperHub HTTP 回覆與本地 execution 狀態。
    - 若 KeeperHub 回覆包含 `status = confirmed` 或 `status = failed`，會自動呼叫 `submit_execution_result()`。

    副作用：
    - 成功 POST 後會先將 execution 標記為 `dispatched`。
    - 若 KeeperHub 已回傳最終結果，會依 confirmed / failed 更新 execution 與訂單。
    """
    request = get_execution_request(execution_id)
    if request["status"] != "proposed":
        raise ValueError(f"execution_id={execution_id} 目前狀態不是 proposed")
    if not request["readyForExecutor"]:
        raise ValueError(f"execution_id={execution_id} 缺少必要欄位：{request['missingFields']}")

    target_url = _resolve_keeperhub_webhook_url(webhook_url)
    keeperhub_response = _post_json(target_url, request["payload"], timeout_seconds, webhook_headers or {})
    dispatch = mark_execution_dispatched(
        execution_id,
        {
            "target": "keeperhub",
            "webhookUrl": target_url,
            "httpStatusCode": keeperhub_response["httpStatusCode"],
            "webhookResponse": keeperhub_response["body"],
        },
    )

    final_result = _extract_keeperhub_execution_result(keeperhub_response["body"])
    if final_result is None:
        if wait_for_final_result:
            wait_result = wait_for_keeperhub_execution_result(
                execution_id,
                poll_interval_seconds=poll_interval_seconds,
                max_wait_seconds=max_wait_seconds,
                timeout_seconds=timeout_seconds,
                status_api_base=status_api_base,
                status_headers=status_headers,
            )
            return {
                "status": "keeperhub_wait_completed",
                "executionId": execution_id,
                "executionStatus": wait_result["executionStatus"],
                "keeperhub": keeperhub_response,
                "dispatchResult": dispatch,
                "waitResult": wait_result,
            }
        return {
            "status": "keeperhub_dispatch_completed",
            "executionId": execution_id,
            "executionStatus": dispatch["executionStatus"],
            "keeperhub": keeperhub_response,
            "dispatchResult": dispatch,
        }

    submit_result = submit_execution_result(execution_id, final_result)
    return {
        "status": "keeperhub_result_accepted",
        "executionId": execution_id,
        "executionStatus": submit_result["executionStatus"],
        "keeperhub": keeperhub_response,
        "dispatchResult": dispatch,
        "submitResult": submit_result,
    }


def get_waiting_keeperhub_executions(limit: int = 20) -> list[dict[str, Any]]:
    """
    取得已送到 KeeperHub、但本地尚未收到最終結果的 executions。

    輸入：
    - `limit`：最多回傳幾筆。

    輸出：
    - 回傳 list，每筆包含本地 `executionId`、KeeperHub execution id 與原始 execution request。

    副作用：
    - 無；此函式只讀取 `executions.db`。
    """
    orchestrator_server._ensure_databases()
    safe_limit = max(1, int(limit))
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM executions
            WHERE status = 'dispatched'
            ORDER BY datetime(updated_at) ASC, id ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    waiting: list[dict[str, Any]] = []
    for row in rows:
        confirmation = _json_loads(row["confirmation_json"]) or {}
        keeperhub_execution_id = _extract_keeperhub_execution_id_from_confirmation(confirmation)
        waiting.append(
            {
                "executionId": row["execution_id"],
                "keeperhubExecutionId": keeperhub_execution_id,
                "dispatchMetadata": confirmation.get("dispatchMetadata") or {},
                "executionRequest": _execution_row_to_request(row),
            }
        )
    return waiting


def refresh_keeperhub_execution_results(
    limit: int = 20,
    timeout_seconds: float = DEFAULT_KEEPERHUB_TIMEOUT_SECONDS,
    status_api_base: str | None = None,
    status_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    自動刷新 KeeperHub running executions，並在拿到最終結果時收尾。

    輸入：
    - `limit`：本次最多檢查幾筆 dispatched execution。
    - `timeout_seconds`：每次 HTTP GET 等待秒數。
    - `status_api_base`：可選 KeeperHub status API base URL。
    - `status_headers`：可選額外 headers。

    輸出：
    - `waiting`：仍在 running / pending，尚未更新本地訂單。
    - `finalized`：本次已依 success / failed 收尾的 execution。
    - `skipped`：缺少 KeeperHub id 或本地已收尾的 execution。
    - `errors`：HTTP 或格式錯誤。

    副作用：
    - 只處理 `executions.status = dispatched` 的資料。
    - KeeperHub 回傳 success 時，呼叫 `submit_execution_result(... confirmed ...)` 正式扣單。
    - KeeperHub 回傳 failed / error / cancelled 時，呼叫 `submit_execution_result(... failed ...)`，不扣單。
    """
    waiting_rows = get_waiting_keeperhub_executions(limit=limit)
    result: dict[str, Any] = {
        "status": "keeperhub_refresh_completed",
        "checkedCount": len(waiting_rows),
        "waiting": [],
        "finalized": [],
        "skipped": [],
        "errors": [],
    }

    seen_execution_ids: set[str] = set()
    for waiting in waiting_rows:
        execution_id = waiting["executionId"]
        if execution_id in seen_execution_ids:
            result["skipped"].append({"executionId": execution_id, "reason": "duplicate_in_batch"})
            continue
        seen_execution_ids.add(execution_id)

        keeperhub_execution_id = waiting.get("keeperhubExecutionId")
        if not keeperhub_execution_id:
            result["skipped"].append({"executionId": execution_id, "reason": "missing_keeperhub_execution_id"})
            continue

        try:
            status_body = fetch_keeperhub_execution_status(
                str(keeperhub_execution_id),
                timeout_seconds=timeout_seconds,
                status_api_base=status_api_base,
                status_headers=status_headers,
            )
            normalized_result = _extract_keeperhub_execution_result(status_body)
        except ValueError as exc:
            result["errors"].append(
                {
                    "executionId": execution_id,
                    "keeperhubExecutionId": keeperhub_execution_id,
                    "error": str(exc),
                }
            )
            continue

        if normalized_result is None:
            keeperhub_status = _extract_keeperhub_status(status_body)
            result["waiting"].append(
                {
                    "executionId": execution_id,
                    "keeperhubExecutionId": keeperhub_execution_id,
                    "keeperhubStatus": keeperhub_status or "unknown",
                    "rawStatus": status_body,
                }
            )
            continue

        submit_result = submit_execution_result(execution_id, normalized_result)
        result["finalized"].append(
            {
                "executionId": execution_id,
                "keeperhubExecutionId": keeperhub_execution_id,
                "executionStatus": submit_result["executionStatus"],
                "keeperhubStatus": _extract_keeperhub_status(status_body),
                "submitResult": submit_result,
            }
        )

    return result


def fetch_keeperhub_execution_status(
    keeperhub_execution_id: str,
    timeout_seconds: float = DEFAULT_KEEPERHUB_TIMEOUT_SECONDS,
    status_api_base: str | None = None,
    status_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    呼叫 KeeperHub status API 取得單筆 workflow execution 狀態。

    輸入：
    - `keeperhub_execution_id`：KeeperHub webhook 回覆中的 execution id。
    - `timeout_seconds`：HTTP GET 等待秒數。
    - `status_api_base`：預設組成 `/api/workflows/executions/{id}/status`。
    - `status_headers`：可選額外 headers。

    輸出：
    - 回傳 KeeperHub status API 的 JSON body。

    錯誤：
    - HTTP 非 2xx 或回覆不是 JSON object 時，丟出 `ValueError`。
    """
    if not keeperhub_execution_id:
        raise ValueError("keeperhub_execution_id 不可為空")
    url = _resolve_keeperhub_status_url(keeperhub_execution_id, status_api_base)
    body = _get_json(url, timeout_seconds, status_headers or {})
    if not isinstance(body, dict):
        raise ValueError("KeeperHub status API 回覆必須是 JSON object")
    return body


def wait_for_keeperhub_execution_result(
    execution_id: str,
    poll_interval_seconds: float = 5.0,
    max_wait_seconds: float = 300.0,
    timeout_seconds: float = DEFAULT_KEEPERHUB_TIMEOUT_SECONDS,
    status_api_base: str | None = None,
    status_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    等待單筆 KeeperHub execution 取得最終結果，並自動收尾。

    輸入：
    - `execution_id`：本地 execution id。
    - `poll_interval_seconds`：KeeperHub 還在 running 時，下一次查詢前等待秒數。
    - `max_wait_seconds`：最多等待秒數，避免 HTTP worker 永久卡住。
    - `timeout_seconds`：每次 KeeperHub status API GET 等待秒數。
    - `status_api_base`：可選 KeeperHub status API base URL。
    - `status_headers`：可選額外 headers。

    輸出：
    - 若取得 success / failed，回傳收尾結果。
    - 若逾時仍是 running，回傳 `keeperhub_wait_timeout`，本地 execution 維持 dispatched。

    副作用：
    - 只處理本地狀態為 `dispatched` 的 execution。
    - 取得最終結果時，透過 `submit_execution_result()` 套用 confirmed / failed。
    """
    row = _fetch_execution_row(execution_id)
    if row is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    if row["status"] in FINAL_EXECUTION_STATUSES:
        return {
            "status": "already_finalized",
            "executionId": execution_id,
            "executionStatus": row["status"],
            "executionRequest": _execution_row_to_request(row),
        }
    if row["status"] != "dispatched":
        raise ValueError(f"execution_id={execution_id} 目前狀態不是 dispatched")

    confirmation = _json_loads(row["confirmation_json"]) or {}
    keeperhub_execution_id = _extract_keeperhub_execution_id_from_confirmation(confirmation)
    if not keeperhub_execution_id:
        raise ValueError(f"execution_id={execution_id} 缺少 KeeperHub execution id")

    started_at = time.monotonic()
    safe_poll_interval = max(0.1, float(poll_interval_seconds))
    safe_max_wait = max(0.0, float(max_wait_seconds))
    last_status_body: dict[str, Any] | None = None

    while True:
        status_body = fetch_keeperhub_execution_status(
            str(keeperhub_execution_id),
            timeout_seconds=timeout_seconds,
            status_api_base=status_api_base,
            status_headers=status_headers,
        )
        last_status_body = status_body
        normalized_result = _extract_keeperhub_execution_result(status_body)
        if normalized_result is not None:
            submit_result = submit_execution_result(execution_id, normalized_result)
            return {
                "status": "keeperhub_final_result_accepted",
                "executionId": execution_id,
                "keeperhubExecutionId": keeperhub_execution_id,
                "keeperhubStatus": _extract_keeperhub_status(status_body),
                "executionStatus": submit_result["executionStatus"],
                "submitResult": submit_result,
            }

        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds >= safe_max_wait:
            return {
                "status": "keeperhub_wait_timeout",
                "executionId": execution_id,
                "keeperhubExecutionId": keeperhub_execution_id,
                "keeperhubStatus": _extract_keeperhub_status(last_status_body) or "unknown",
                "executionStatus": "dispatched",
                "lastStatus": last_status_body,
            }

        time.sleep(min(safe_poll_interval, safe_max_wait - elapsed_seconds))


def run_cli() -> None:
    """
    區塊鏈端 execution message 介面 CLI。

    輸入：
    - `pending`：列出待送出的交易請求。
    - `get`：取得單筆交易請求。
    - `dispatch`：標記已送給區塊鏈端。
    - `submit`：提交區塊鏈端結果 JSON。

    輸出：
    - 將結果以 JSON 印到 stdout。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork execution message interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pending_parser = subparsers.add_parser("pending")
    pending_parser.add_argument("--limit", type=int, default=20)
    pending_parser.add_argument("--ready-only", action="store_true")

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("execution_id")

    dispatch_parser = subparsers.add_parser("dispatch")
    dispatch_parser.add_argument("execution_id")
    dispatch_parser.add_argument("--metadata-json", default="{}")

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("execution_id")
    submit_parser.add_argument("--result-json", required=True)

    keeperhub_parser = subparsers.add_parser("keeperhub")
    keeperhub_parser.add_argument("execution_id")
    keeperhub_parser.add_argument("--webhook-url")
    keeperhub_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_KEEPERHUB_TIMEOUT_SECONDS)
    keeperhub_parser.add_argument("--wait-for-final-result", action="store_true")
    keeperhub_parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    keeperhub_parser.add_argument("--max-wait-seconds", type=float, default=300.0)

    refresh_parser = subparsers.add_parser("keeperhub-refresh")
    refresh_parser.add_argument("--limit", type=int, default=20)
    refresh_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_KEEPERHUB_TIMEOUT_SECONDS)
    refresh_parser.add_argument("--status-api-base")

    wait_parser = subparsers.add_parser("keeperhub-wait")
    wait_parser.add_argument("execution_id")
    wait_parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    wait_parser.add_argument("--max-wait-seconds", type=float, default=300.0)
    wait_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_KEEPERHUB_TIMEOUT_SECONDS)
    wait_parser.add_argument("--status-api-base")

    args = parser.parse_args()
    if args.command == "pending":
        output = get_pending_execution_requests(limit=args.limit, ready_only=args.ready_only)
    elif args.command == "get":
        output = get_execution_request(args.execution_id)
    elif args.command == "dispatch":
        output = mark_execution_dispatched(args.execution_id, json.loads(args.metadata_json))
    elif args.command == "submit":
        output = submit_execution_result(args.execution_id, json.loads(args.result_json))
    elif args.command == "keeperhub":
        output = send_execution_to_keeperhub(
            args.execution_id,
            webhook_url=args.webhook_url,
            timeout_seconds=args.timeout_seconds,
            wait_for_final_result=args.wait_for_final_result,
            poll_interval_seconds=args.poll_interval_seconds,
            max_wait_seconds=args.max_wait_seconds,
        )
    elif args.command == "keeperhub-refresh":
        output = refresh_keeperhub_execution_results(
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
            status_api_base=args.status_api_base,
        )
    elif args.command == "keeperhub-wait":
        output = wait_for_keeperhub_execution_result(
            args.execution_id,
            poll_interval_seconds=args.poll_interval_seconds,
            max_wait_seconds=args.max_wait_seconds,
            timeout_seconds=args.timeout_seconds,
            status_api_base=args.status_api_base,
        )
    else:
        raise ValueError(f"未知 command：{args.command}")
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


def _execution_row_to_request(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 executions row 轉成區塊鏈端可讀 request。

    輸入：
    - `row`：`executions` 的 SQLite Row。

    輸出：
    - 回傳 execution request。
    - `payload` 是唯一要交給區塊鏈端的嚴格格式資料。
    """
    proposal = _json_loads(row["proposal_json"]) or {}
    raw_payload = _json_loads(row["execution_payload_json"]) or {}
    strict_payload = _strict_blockchain_payload(raw_payload)
    missing_fields = _missing_blockchain_payload_fields(strict_payload)
    return {
        "executionId": row["execution_id"],
        "taskId": row["task_id"],
        "sellOrderId": row["sell_order_id"],
        "status": row["status"],
        "missingFields": missing_fields,
        "readyForExecutor": not missing_fields,
        "payload": strict_payload,
        "proposal": proposal,
        "failureReason": row["failure_reason"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "confirmedAt": row["confirmed_at"],
    }


def _strict_blockchain_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """
    取出真正要交給區塊鏈端的嚴格 payload。

    輸入：
    - `raw_payload`：資料庫裡記錄的 execution payload。

    輸出：
    - 若是新版格式，原樣回傳。
    - 若是舊版 envelope，回傳其中的 `AI_to_Backend_Payload`。
    """
    if isinstance(raw_payload.get("AI_to_Backend_Payload"), dict):
        payload = raw_payload["AI_to_Backend_Payload"]
    else:
        payload = raw_payload

    if isinstance(payload.get("routeDetails"), dict) and "Calldata" not in payload["routeDetails"]:
        payload = dict(payload)
        payload["routeDetails"] = dict(payload["routeDetails"])
        payload["routeDetails"]["Calldata"] = None
    return payload


def _missing_blockchain_payload_fields(payload: dict[str, Any]) -> list[str]:
    """
    檢查嚴格區塊鏈 payload 是否缺欄位。

    輸入：
    - `payload`：格式為 `intentA`、`actionType`、`executeAmountIn`、`routeDetails` 的 dict。
    - `routeDetails` 固定包含 `Calldata`、`matchedIntentB`、`treasuryAmountOut`。

    輸出：
    - 回傳缺少欄位路徑；空 list 代表可交給區塊鏈端。
    """
    missing: list[str] = []
    if not isinstance(payload.get("intentA"), dict):
        missing.append("intentA")
        return missing

    intent_a = payload["intentA"].get("intent")
    if not isinstance(intent_a, dict):
        missing.append("intentA.intent")
    else:
        for field in REQUIRED_INTENT_FIELDS:
            if intent_a.get(field) in (None, ""):
                missing.append(f"intentA.intent.{field}")
    if not payload["intentA"].get("signature"):
        missing.append("intentA.signature")

    if payload.get("actionType") not in (0, 1, 2):
        missing.append("actionType")
    if payload.get("executeAmountIn") in (None, ""):
        missing.append("executeAmountIn")

    route_details = payload.get("routeDetails")
    if not isinstance(route_details, dict):
        missing.append("routeDetails")
        return missing
    if "Calldata" not in route_details:
        missing.append("routeDetails.Calldata")
    elif payload.get("actionType") == 0 and route_details.get("Calldata") in (None, ""):
        missing.append("routeDetails.Calldata")

    if payload.get("actionType") == 1:
        matched_intent_b = route_details.get("matchedIntentB")
        if not isinstance(matched_intent_b, dict):
            missing.append("routeDetails.matchedIntentB")
        else:
            intent_b = matched_intent_b.get("intent")
            if not isinstance(intent_b, dict):
                missing.append("routeDetails.matchedIntentB.intent")
            else:
                for field in REQUIRED_INTENT_FIELDS:
                    if intent_b.get(field) in (None, ""):
                        missing.append(f"routeDetails.matchedIntentB.intent.{field}")
            if not matched_intent_b.get("signature"):
                missing.append("routeDetails.matchedIntentB.signature")
            if matched_intent_b.get("executeAmountInB") in (None, ""):
                missing.append("routeDetails.matchedIntentB.executeAmountInB")

    if payload.get("actionType") == 2 and route_details.get("treasuryAmountOut") in (None, ""):
        missing.append("routeDetails.treasuryAmountOut")

    return missing


def _fetch_execution_row(execution_id: str) -> sqlite3.Row | None:
    """
    讀取 execution row。

    輸入：
    - `execution_id`：execution id。

    輸出：
    - 找到時回傳 `sqlite3.Row`，找不到時回傳 `None`。
    """
    orchestrator_server._ensure_databases()
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()


def _normalize_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    """
    正規化區塊鏈端回報。

    輸入：
    - `result`：外部回報 dict。

    輸出：
    - 回傳 `orchestrator_server.confirm_execution()` 可接受的 confirmation dict。
    """
    status = str(result.get("status") or "").strip()
    if status not in ("confirmed", "failed"):
        raise ValueError("execution result status 必須是 confirmed 或 failed")

    if status == "failed":
        return {
            "status": "failed",
            "failureReason": result.get("failureReason") or result.get("failure_reason") or "execution failed",
            "rawResult": result,
        }

    return {
        "status": "confirmed",
        "txHash": result.get("txHash") or result.get("tx_hash"),
        "blockNumber": result.get("blockNumber") or result.get("block_number"),
        "rawReceipt": result.get("rawReceipt") or result.get("raw_receipt"),
        "notes": result.get("notes") or "blockchain executor confirmed execution",
        "rawResult": result,
    }


def _resolve_keeperhub_webhook_url(webhook_url: str | None = None) -> str:
    """
    取得 KeeperHub webhook URL。

    輸入：
    - `webhook_url`：呼叫端明確指定的 URL。

    輸出：
    - 回傳實際要 POST 的 URL。
    """
    _load_env()
    resolved = webhook_url or os.getenv(KEEPERHUB_WEBHOOK_URL_ENV) or DEFAULT_KEEPERHUB_WEBHOOK_URL
    if not resolved.startswith("https://"):
        raise ValueError("KeeperHub webhook URL 必須是 https://")
    return resolved


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    POST JSON 到外部 webhook。

    輸入：
    - `url`：目標 URL。
    - `payload`：要送出的嚴格區塊鏈 payload。
    - `timeout_seconds`：等待秒數。
    - `extra_headers`：呼叫端額外提供的 HTTP headers。

    輸出：
    - 回傳 HTTP 狀態碼與解析後 body。
    """
    headers = _keeperhub_headers(extra_headers or {})
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"KeeperHub webhook 發送失敗：{exc}") from exc

    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return {"httpStatusCode": response.status_code, "body": body}


def _get_json(
    url: str,
    timeout_seconds: float,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """
    GET JSON 外部 API。

    輸入：
    - `url`：目標 URL。
    - `timeout_seconds`：等待秒數。
    - `extra_headers`：呼叫端額外 headers。

    輸出：
    - 回傳解析後 JSON body。
    """
    headers = _keeperhub_status_headers(extra_headers or {})
    try:
        response = httpx.get(url, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"KeeperHub status API 讀取失敗：{exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise ValueError("KeeperHub status API 回覆不是 JSON") from exc


def _keeperhub_headers(extra_headers: dict[str, str]) -> dict[str, str]:
    """
    建立 KeeperHub webhook HTTP headers。

    輸入：
    - `extra_headers`：內部 API 或 CLI 呼叫端提供的額外 headers。

    輸出：
    - 回傳實際送出的 headers。

    支援 `.env`：
    - `KEEPERHUB_WEBHOOK_AUTHORIZATION`：完整 Authorization header 值。
    - `KEEPERHUB_WEBHOOK_TOKEN`：Bearer token，會自動組成 `Authorization: Bearer ...`。
    """
    _load_env()
    headers = {
        "Accept": "application/json",
        "User-Agent": "CactusNetwork-Backend/0.1 (+https://github.com/foodpenguin/CactusNetwork)",
        **extra_headers,
    }
    if "Authorization" not in headers:
        authorization = os.getenv("KEEPERHUB_WEBHOOK_AUTHORIZATION")
        token = os.getenv("KEEPERHUB_WEBHOOK_TOKEN")
        if authorization:
            headers["Authorization"] = authorization
        elif token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _keeperhub_status_headers(extra_headers: dict[str, str]) -> dict[str, str]:
    """
    建立 KeeperHub status API HTTP headers。

    輸入：
    - `extra_headers`：呼叫端額外提供的 headers。

    輸出：
    - 回傳實際送出的 headers。

    支援 `.env`：
    - `KEEPERHUB_STATUS_AUTHORIZATION` 或 `KEEPERHUB_API_AUTHORIZATION`：完整 Authorization header 值。
    - `KEEPERHUB_STATUS_TOKEN` 或 `KEEPERHUB_API_TOKEN`：Bearer token。
    - 若未設定 status token，最後才退回 `KEEPERHUB_WEBHOOK_TOKEN`。
    """
    _load_env()
    headers = {
        "Accept": "application/json",
        "User-Agent": "CactusNetwork-Backend/0.1 (+https://github.com/foodpenguin/CactusNetwork)",
        **extra_headers,
    }
    if "Authorization" not in headers:
        authorization = os.getenv("KEEPERHUB_STATUS_AUTHORIZATION") or os.getenv("KEEPERHUB_API_AUTHORIZATION")
        token = os.getenv("KEEPERHUB_STATUS_TOKEN") or os.getenv("KEEPERHUB_API_TOKEN") or os.getenv("KEEPERHUB_WEBHOOK_TOKEN")
        if authorization:
            headers["Authorization"] = authorization
        elif token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_keeperhub_execution_result(body: Any) -> dict[str, Any] | None:
    """
    從 KeeperHub 回覆中取出 confirmed / failed 結果。

    輸入：
    - `body`：KeeperHub webhook 回覆 body。

    輸出：
    - 若找到 `status = confirmed` 或 `status = failed`，回傳可交給 `submit_execution_result()` 的 dict。
    - 若 KeeperHub 只回覆接收成功，回傳 `None`。
    """
    if not isinstance(body, dict):
        return None

    candidates = [
        body,
        body.get("result"),
        body.get("data"),
        body.get("executionResult"),
        body.get("receipt"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        status = _extract_keeperhub_status(candidate)
        if status in ("confirmed", "failed"):
            return candidate
        if status in KEEPERHUB_SUCCESS_STATUSES:
            return {
                "status": "confirmed",
                "tx_hash": candidate.get("txHash") or candidate.get("tx_hash"),
                "block_number": candidate.get("blockNumber") or candidate.get("block_number"),
                "raw_receipt": candidate,
                "notes": "KeeperHub execution succeeded",
            }
        if status in KEEPERHUB_FAILED_STATUSES:
            return {
                "status": "failed",
                "failure_reason": (
                    candidate.get("failureReason")
                    or candidate.get("failure_reason")
                    or candidate.get("error")
                    or candidate.get("message")
                    or f"KeeperHub execution {status}"
                ),
                "raw_receipt": candidate,
            }
    return None


def _extract_keeperhub_status(body: Any) -> str | None:
    """
    從 KeeperHub 回覆取出狀態字串。

    輸入：
    - `body`：KeeperHub 回覆或其子物件。

    輸出：
    - 找到時回傳小寫 status，找不到時回傳 `None`。
    """
    if not isinstance(body, dict):
        return None
    for key in ("status", "state", "executionStatus"):
        value = body.get(key)
        if value not in (None, ""):
            return str(value).strip().lower()
    nested = body.get("data") or body.get("result") or body.get("execution")
    if isinstance(nested, dict):
        return _extract_keeperhub_status(nested)
    return None


def _extract_keeperhub_execution_id_from_confirmation(confirmation: dict[str, Any]) -> str | None:
    """
    從本地 dispatch metadata 中取出 KeeperHub execution id。

    輸入：
    - `confirmation`：`executions.confirmation_json` 解析後的 dict。

    輸出：
    - 找到時回傳 KeeperHub execution id，找不到時回傳 `None`。
    """
    dispatch_metadata = confirmation.get("dispatchMetadata") if isinstance(confirmation, dict) else None
    if not isinstance(dispatch_metadata, dict):
        return None
    return _extract_keeperhub_execution_id(dispatch_metadata.get("webhookResponse")) or _extract_keeperhub_execution_id(dispatch_metadata)


def _extract_keeperhub_execution_id(body: Any) -> str | None:
    """
    從 KeeperHub webhook 回覆取出 execution id。

    輸入：
    - `body`：KeeperHub webhook 或 status API 回覆 body。

    輸出：
    - 找到時回傳 id，找不到時回傳 `None`。
    """
    if not isinstance(body, dict):
        return None
    for key in ("executionId", "execution_id", "workflowExecutionId", "workflow_execution_id", "id"):
        value = body.get(key)
        if value not in (None, ""):
            return str(value)
    for key in ("data", "result", "execution", "workflowExecution"):
        nested = body.get(key)
        nested_id = _extract_keeperhub_execution_id(nested)
        if nested_id:
            return nested_id
    return None


def _resolve_keeperhub_status_url(keeperhub_execution_id: str, status_api_base: str | None = None) -> str:
    """
    組出 KeeperHub status API URL。

    輸入：
    - `keeperhub_execution_id`：KeeperHub execution id。
    - `status_api_base`：可選 base URL 或包含 `{execution_id}` 的完整 URL template。

    輸出：
    - 回傳完整 HTTPS URL。
    """
    _load_env()
    base = status_api_base or os.getenv(KEEPERHUB_STATUS_API_BASE_ENV) or DEFAULT_KEEPERHUB_STATUS_API_BASE
    if "{execution_id}" in base:
        url = base.format(execution_id=keeperhub_execution_id)
    else:
        url = f"{base.rstrip('/')}/{keeperhub_execution_id}/status"
    if not url.startswith("https://"):
        raise ValueError("KeeperHub status API URL 必須是 https://")
    return url


def _load_env(path: Path = ENV_FILE) -> None:
    """
    讀取 `.env` 到目前 process。

    輸入：
    - `path`：`.env` 路徑。

    輸出：
    - 無。
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_loads(raw_json: str | None) -> Any:
    """
    解析 JSON 字串。

    輸入：
    - `raw_json`：JSON 字串或空值。

    輸出：
    - 回傳解析結果；空值回傳 `None`。
    """
    if raw_json in (None, ""):
        return None
    return json.loads(raw_json)


def _now() -> str:
    """
    取得目前 UTC ISO 時間。

    輸入：
    - 無。

    輸出：
    - 回傳 ISO 時間字串。
    """
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    run_cli()
