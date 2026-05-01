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

from scripts import blockchain_sync
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
ONCHAIN_PREFLIGHT_ENV = "ONCHAIN_PREFLIGHT_CHECKS"


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


def confirm_execution_from_onchain(
    execution_id: str,
    tx_hash: str | None = None,
    raw_keeperhub_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    讀取鏈上狀態並以鏈上資料作為 execution 最終判定。

    輸入：
    - `execution_id`：本地 execution id。
    - `tx_hash`：可選；KeeperHub 或 executor 若有提供交易 hash，可一併記錄。
    - `raw_keeperhub_result`：可選；KeeperHub 原始成功回覆，用於除錯留存。

    輸出：
    - 鏈上 filled amount 足夠時，回傳 `onchain_confirmation_accepted` 並正式 confirmed。
    - 鏈上資料不足或設定缺失時，回傳 `onchain_confirmation_not_found`，不更新本地訂單。

    副作用：
    - confirmed 時會呼叫 `submit_execution_result()`，此時才扣除本地買賣單剩餘量。
    - not_found 時只回報原因，不把 execution 標成 failed，讓呼叫端可稍後重查。
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
    if row["status"] not in ("proposed", "dispatched"):
        raise ValueError(f"execution_id={execution_id} 目前狀態不是 proposed 或 dispatched")

    confirmation_result = _build_onchain_confirmation_result(
        execution_id,
        tx_hash=tx_hash,
        raw_keeperhub_result=raw_keeperhub_result,
    )
    if confirmation_result["status"] != "confirmed":
        return {
            "status": "onchain_confirmation_not_found",
            "executionId": execution_id,
            "executionStatus": row["status"],
            "failureReason": confirmation_result.get("failure_reason") or confirmation_result.get("failureReason"),
            "onchainEvidence": confirmation_result.get("onchainEvidence"),
            "confirmationCandidate": confirmation_result,
        }

    submit_result = _submit_final_keeperhub_result(execution_id, confirmation_result)
    return {
        "status": "onchain_confirmation_accepted",
        "executionId": execution_id,
        "executionStatus": submit_result["executionStatus"],
        "submitResult": submit_result,
        "onchainEvidence": confirmation_result.get("onchainEvidence"),
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
    run_onchain_preflight: bool = True,
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
    if run_onchain_preflight:
        preflight = check_execution_payload_onchain_preflight(request["payload"])
        if preflight["status"] == "failed":
            raise ValueError(f"鏈上預檢失敗：{preflight['failureReason']}")
        if preflight["status"] == "error":
            raise ValueError(f"鏈上預檢錯誤：{preflight['failureReason']}")

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

    final_result = _resolve_keeperhub_final_result(execution_id, final_result)
    submit_result = submit_execution_result(execution_id, final_result)
    return {
        "status": "keeperhub_result_accepted",
        "executionId": execution_id,
        "executionStatus": submit_result["executionStatus"],
        "keeperhub": keeperhub_response,
        "dispatchResult": dispatch,
        "submitResult": submit_result,
    }


def check_execution_payload_onchain_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """
    在送 KeeperHub 前讀鏈上狀態，確認 vault 餘額與 intent 剩餘額度足夠。

    輸入：
    - `payload`：嚴格 execution payload。

    輸出：
    - `status = passed`：鏈上檢查通過，可送 KeeperHub。
    - `status = failed`：查詢成功但餘額或 remaining 不足，不應送出。
    - `status = error`：RPC 或格式錯誤，暫不送出，避免誤判。
    - `status = skipped`：未啟用或缺少必要 env，維持舊流程。

    副作用：
    - 啟用時會發送 JSON-RPC `eth_call`。
    - 不送交易，不改本地或鏈上狀態。
    """
    mode = _onchain_preflight_mode()
    if mode == "disabled":
        return {"status": "skipped", "ready": True, "reason": "ONCHAIN_PREFLIGHT_CHECKS disabled", "checks": []}

    config = _onchain_preflight_config()
    missing_config = [key for key, value in config.items() if key in {"rpcUrl", "vaultAddress", "routerAddress"} and not value]
    if missing_config:
        if mode == "required":
            return {
                "status": "error",
                "ready": False,
                "failureReason": "鏈上預檢缺少設定：" + ", ".join(missing_config),
                "checks": [],
            }
        return {
            "status": "skipped",
            "ready": True,
            "reason": "鏈上預檢 auto 模式缺少設定：" + ", ".join(missing_config),
            "checks": [],
        }

    checks: list[dict[str, Any]] = []
    try:
        checks.append(
            _check_intent_capacity(
                "intentA",
                (payload.get("intentA") or {}).get("intent"),
                payload.get("executeAmountIn"),
                config,
            )
        )
        route_details = payload.get("routeDetails") or {}
        matched_intent_b = route_details.get("matchedIntentB")
        if payload.get("actionType") == 1 and isinstance(matched_intent_b, dict):
            checks.append(
                _check_intent_capacity(
                    "routeDetails.matchedIntentB",
                    matched_intent_b.get("intent"),
                    matched_intent_b.get("executeAmountInB"),
                    config,
                )
            )
        if payload.get("actionType") == 2:
            checks.append(_check_treasury_capacity(payload, config))
    except Exception as exc:
        return {
            "status": "error",
            "ready": False,
            "failureReason": str(exc),
            "checks": checks,
        }

    failed_checks = [check for check in checks if not check.get("isExecutable", False)]
    if failed_checks:
        return {
            "status": "failed",
            "ready": False,
            "failureReason": _format_onchain_preflight_failure(failed_checks),
            "checks": checks,
        }
    return {
        "status": "passed",
        "ready": True,
        "checks": checks,
    }


def check_execution_payload_onchain_confirmation(payload: dict[str, Any]) -> dict[str, Any]:
    """
    在 KeeperHub 回覆後讀鏈上狀態，確認本次 payload 是否已被 SettlementRouter 記帳。

    輸入：
    - `payload`：嚴格 execution payload。

    輸出：
    - `status = confirmed`：每個需要成交的 intent，其 `filledAmountIn` 都已達本次執行量。
    - `status = failed`：RPC 可讀，但鏈上 filled amount 尚未達標。
    - `status = error`：缺少 RPC / Router 設定或 payload 格式無法檢查。

    副作用：
    - 發送 JSON-RPC `eth_call` 讀取 `SettlementRouter.filledAmountIn(intentHash)`。
    - 不送交易、不改鏈上狀態。
    """
    config = _onchain_preflight_config()
    missing_config = [key for key, value in config.items() if key in {"rpcUrl", "routerAddress"} and not value]
    if missing_config:
        return {
            "status": "error",
            "confirmed": False,
            "failureReason": "鏈上確認缺少設定：" + ", ".join(missing_config),
            "checks": [],
        }

    missing_fields = _missing_blockchain_payload_fields(payload)
    if missing_fields:
        return {
            "status": "error",
            "confirmed": False,
            "failureReason": "鏈上確認 payload 缺少欄位：" + ", ".join(missing_fields),
            "checks": [],
        }

    checks: list[dict[str, Any]] = []
    try:
        checks.append(
            _check_intent_fill_confirmation(
                "intentA",
                (payload.get("intentA") or {}).get("intent"),
                payload.get("executeAmountIn"),
                config,
            )
        )
        route_details = payload.get("routeDetails") or {}
        matched_intent_b = route_details.get("matchedIntentB")
        if payload.get("actionType") == 1 and isinstance(matched_intent_b, dict):
            checks.append(
                _check_intent_fill_confirmation(
                    "routeDetails.matchedIntentB",
                    matched_intent_b.get("intent"),
                    matched_intent_b.get("executeAmountInB"),
                    config,
                )
            )
    except Exception as exc:
        return {
            "status": "error",
            "confirmed": False,
            "failureReason": str(exc),
            "checks": checks,
        }

    failed_checks = [check for check in checks if not check.get("hasFilledRequiredAmount", False)]
    if failed_checks:
        return {
            "status": "failed",
            "confirmed": False,
            "failureReason": _format_onchain_confirmation_failure(failed_checks),
            "checks": checks,
        }
    return {
        "status": "confirmed",
        "confirmed": True,
        "checks": checks,
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

        normalized_result = _resolve_keeperhub_final_result(execution_id, normalized_result)
        submit_result = _submit_final_keeperhub_result(execution_id, normalized_result)
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
            normalized_result = _resolve_keeperhub_final_result(execution_id, normalized_result)
            submit_result = _submit_final_keeperhub_result(execution_id, normalized_result)
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


def _submit_final_keeperhub_result(execution_id: str, normalized_result: dict[str, Any]) -> dict[str, Any]:
    """
    套用 KeeperHub 最終結果，若本地狀態已無法 confirmed，改以 failed 收尾釋放鎖。

    輸入：
    - `execution_id`：本地 execution id。
    - `normalized_result`：KeeperHub success / failed 正規化結果。

    輸出：
    - 回傳 `submit_execution_result()` 的結果。
    - 若 KeeperHub 回 success 但本地訂單已 timeout / invalid / filled，會回傳 failed 結果並附上 `localApplyError`。

    副作用：
    - confirmed 可套用時正式扣單。
    - confirmed 不可套用或 failed 時，將 execution 標成 failed，避免永久鎖住買賣單。
    """
    try:
        return submit_execution_result(execution_id, normalized_result)
    except ValueError as exc:
        fallback = submit_execution_result(
            execution_id,
            {
                "status": "failed",
                "failure_reason": f"KeeperHub final result could not be applied locally: {exc}",
                "raw_keeperhub_result": normalized_result,
            },
        )
        fallback["localApplyError"] = str(exc)
        return fallback


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

    route_details = payload.get("routeDetails") if isinstance(payload.get("routeDetails"), dict) else {}
    matched_intent_b = route_details.get("matchedIntentB")

    strict_route_details: dict[str, Any] = {
        "Calldata": route_details.get("Calldata"),
        "matchedIntentB": None,
        "treasuryAmountOut": route_details.get("treasuryAmountOut"),
    }
    if isinstance(matched_intent_b, dict):
        strict_route_details["matchedIntentB"] = {
            "intent": _strict_intent(matched_intent_b.get("intent")),
            "signature": _normalize_hex_string(matched_intent_b.get("signature")),
            "executeAmountInB": _string_or_none(matched_intent_b.get("executeAmountInB")),
        }

    intent_a = payload.get("intentA") if isinstance(payload.get("intentA"), dict) else {}
    return {
        "intentA": {
            "intent": _strict_intent(intent_a.get("intent")),
            "signature": _normalize_hex_string(intent_a.get("signature")),
        },
        "actionType": payload.get("actionType"),
        "executeAmountIn": _string_or_none(payload.get("executeAmountIn")),
        "routeDetails": strict_route_details,
    }


def _strict_intent(value: Any) -> dict[str, Any] | None:
    """
    將 intent 正規化成鏈上端允許的 8 個欄位。

    輸入：
    - `value`：可能含前端輔助欄位的 intent dict。

    輸出：
    - 只包含 `user`、`tokenIn`、`tokenOut`、`amountIn`、`minAmountOut`、`deadline`、`salt`、`allowPartialFill`。
    - 輸入不是 dict 時回傳 `None`。
    """
    if not isinstance(value, dict):
        return None
    return {
        "user": value.get("user"),
        "tokenIn": value.get("tokenIn"),
        "tokenOut": value.get("tokenOut"),
        "amountIn": _string_or_none(value.get("amountIn")),
        "minAmountOut": _string_or_none(value.get("minAmountOut")),
        "deadline": _integer_or_none(value.get("deadline")),
        "salt": _normalize_hex_string(value.get("salt")),
        "allowPartialFill": value.get("allowPartialFill"),
    }


def _normalize_hex_string(value: Any) -> str | None:
    """
    正規化 hex 字串，缺少 `0x` 時自動補上。

    輸入：
    - `value`：signature、salt 或 calldata 類 hex 值。

    輸出：
    - 空值回傳 `None`。
    - 既有 `0x` 前綴時原樣回傳。
    - 純 hex 字串補成 `0x...`。
    """
    if value in (None, ""):
        return None
    text = str(value)
    if text.startswith(("0x", "0X")):
        return "0x" + text[2:]
    return f"0x{text}"


def _integer_or_none(value: Any) -> int | None:
    """
    將 JSON payload 中應為整數的欄位正規化。

    輸入：
    - `value`：可能是 int 或整數字串。

    輸出：
    - 可轉換時回傳 `int`；空值或不可轉換時回傳 `None`。
    """
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    """
    將 JSON 數值欄位轉成字串。

    輸入：
    - `value`：任意值。

    輸出：
    - 空值回傳 `None`，其他值回傳 `str(value)`。
    """
    if value in (None, ""):
        return None
    return str(value)


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


def _onchain_preflight_mode() -> str:
    """
    取得鏈上預檢模式。

    輸入：
    - 無；讀 `.env` / 環境變數 `ONCHAIN_PREFLIGHT_CHECKS`。

    輸出：
    - `disabled`：不做鏈上預檢。
    - `auto`：RPC、Vault、Router 設定齊全才做檢查。
    - `required`：設定缺失或 RPC 錯誤都視為不可送出。
    """
    _load_env()
    raw_mode = str(os.getenv(ONCHAIN_PREFLIGHT_ENV, "auto")).strip().lower()
    if raw_mode in {"0", "false", "off", "disabled", "disable"}:
        return "disabled"
    if raw_mode in {"1", "true", "on", "enabled", "enable", "required", "require"}:
        return "required"
    return "auto"


def _onchain_preflight_config() -> dict[str, str | None]:
    """
    取得鏈上預檢需要的 RPC 與合約地址設定。

    輸入：
    - 無；讀 `.env` / 環境變數。

    輸出：
    - `rpcUrl`
    - `vaultAddress`
    - `routerAddress`
    - `treasuryAddress`
    """
    blockchain_sync.load_env_file()
    return {
        "rpcUrl": os.getenv("SP_TESTNET_RPC_URL") or os.getenv("SEPOLIA_RPC_URL") or os.getenv("RPC_URL"),
        "vaultAddress": os.getenv("INTENT_VAULT_ADDRESS"),
        "routerAddress": os.getenv("SETTLEMENT_ROUTER_ADDRESS"),
        "treasuryAddress": os.getenv("PROTOCOL_TREASURY_ADDRESS"),
    }


def _check_intent_capacity(
    label: str,
    intent: Any,
    execute_amount_in: Any,
    config: dict[str, str | None],
) -> dict[str, Any]:
    """
    讀取單側 intent 的 vault balance 與 filledAmountIn 並判斷是否足夠。

    輸入：
    - `label`：檢查對象名稱，例如 `intentA`。
    - `intent`：UserIntent dict。
    - `execute_amount_in`：本次要消耗的 tokenIn raw amount。
    - `config`：RPC / Vault / Router 設定。

    輸出：
    - 回傳單側鏈上容量檢查結果。
    """
    if not isinstance(intent, dict):
        raise ValueError(f"{label}.intent 不是 object")
    state = blockchain_sync.read_intent_execution_capacity(
        intent,
        execute_amount_in,
        rpc_url=config["rpcUrl"],
        vault_address=config["vaultAddress"],
        router_address=config["routerAddress"],
    )
    return {
        "label": label,
        **state,
    }


def _check_intent_fill_confirmation(
    label: str,
    intent: Any,
    execute_amount_in: Any,
    config: dict[str, str | None],
) -> dict[str, Any]:
    """
    讀取單側 intent 的 filledAmountIn，判斷鏈上是否已記錄本次成交量。

    輸入：
    - `label`：檢查對象名稱，例如 `intentA`。
    - `intent`：UserIntent dict。
    - `execute_amount_in`：本次應成交的 tokenIn raw amount。
    - `config`：RPC / Router 設定。

    輸出：
    - 回傳 intent hash、目前 filled amount、要求成交量，以及是否已達標。
    """
    if not isinstance(intent, dict):
        raise ValueError(f"{label}.intent 不是 object")
    intent_hash = blockchain_sync.hash_intent(intent)
    filled_data = blockchain_sync._call_data(  # noqa: SLF001 - 低階 ABI helper 集中在 blockchain_sync。
        blockchain_sync.ROUTER_FILLED_AMOUNT_IN_SELECTOR,
        [blockchain_sync._encode_bytes32(intent_hash)],  # noqa: SLF001
    )
    filled_amount = int(
        blockchain_sync._decode_uint256(  # noqa: SLF001
            blockchain_sync._eth_call(str(config["routerAddress"]), filled_data, config["rpcUrl"])  # noqa: SLF001
        )
        or "0"
    )
    required_amount = int(str(execute_amount_in))
    amount_in = int(str(intent["amountIn"]))
    return {
        "label": label,
        "intentHash": intent_hash,
        "user": intent["user"],
        "tokenIn": intent["tokenIn"],
        "executeAmountIn": str(required_amount),
        "amountIn": str(amount_in),
        "filledAmountIn": str(filled_amount),
        "remainingAmountIn": str(max(amount_in - filled_amount, 0)),
        "requiredFilledAmountIn": str(required_amount),
        "hasFilledRequiredAmount": filled_amount >= required_amount,
    }


def _check_treasury_capacity(payload: dict[str, Any], config: dict[str, str | None]) -> dict[str, Any]:
    """
    檢查 actionType=2 時 treasury 是否有足夠 tokenOut。

    輸入：
    - `payload`：嚴格 execution payload。
    - `config`：RPC / Treasury 設定。

    輸出：
    - 回傳 treasury tokenOut 餘額檢查結果。
    """
    treasury_address = config.get("treasuryAddress")
    rpc_url = config.get("rpcUrl")
    intent = (payload.get("intentA") or {}).get("intent") or {}
    token_out = intent.get("tokenOut")
    treasury_amount_out = (payload.get("routeDetails") or {}).get("treasuryAmountOut")
    if not treasury_address:
        raise ValueError("actionType=2 鏈上預檢缺少 PROTOCOL_TREASURY_ADDRESS")
    if not token_out:
        raise ValueError("actionType=2 鏈上預檢缺少 intentA.intent.tokenOut")
    if treasury_amount_out in (None, ""):
        raise ValueError("actionType=2 鏈上預檢缺少 routeDetails.treasuryAmountOut")

    data = blockchain_sync._call_data(  # noqa: SLF001 - 後端低階 ABI helper 目前集中在 blockchain_sync。
        blockchain_sync.BALANCE_OF_SELECTOR,
        [blockchain_sync._encode_address(str(treasury_address))],  # noqa: SLF001
    )
    balance = int(blockchain_sync._decode_uint256(blockchain_sync._eth_call(str(token_out), data, rpc_url)) or "0")  # noqa: SLF001
    required = int(str(treasury_amount_out))
    return {
        "label": "routeDetails.treasuryAmountOut",
        "treasuryAddress": treasury_address,
        "tokenOut": token_out,
        "treasuryBalance": str(balance),
        "treasuryAmountOut": str(required),
        "hasEnoughTreasuryBalance": balance >= required,
        "isExecutable": balance >= required,
    }


def _format_onchain_preflight_failure(failed_checks: list[dict[str, Any]]) -> str:
    """
    將鏈上預檢失敗結果整理成 operation_note / error 可讀文字。

    輸入：
    - `failed_checks`：`isExecutable = false` 的檢查項目。

    輸出：
    - 繁體中文失敗原因。
    """
    reasons: list[str] = []
    for check in failed_checks:
        label = check.get("label", "unknown")
        if check.get("hasEnoughVaultBalance") is False:
            reasons.append(
                f"{label} vaultBalance={check.get('vaultBalance')} < executeAmountIn={check.get('executeAmountIn')}"
            )
        if check.get("hasEnoughRemainingAmount") is False:
            reasons.append(
                f"{label} remainingAmountIn={check.get('remainingAmountIn')} < executeAmountIn={check.get('executeAmountIn')}"
            )
        if check.get("hasEnoughTreasuryBalance") is False:
            reasons.append(
                f"{label} treasuryBalance={check.get('treasuryBalance')} < treasuryAmountOut={check.get('treasuryAmountOut')}"
            )
    return "；".join(reasons) or "鏈上預檢未通過"


def _format_onchain_confirmation_failure(failed_checks: list[dict[str, Any]]) -> str:
    """
    將鏈上確認失敗結果整理成可讀原因。

    輸入：
    - `failed_checks`：`hasFilledRequiredAmount = false` 的檢查項目。

    輸出：
    - 繁體中文失敗原因。
    """
    reasons: list[str] = []
    for check in failed_checks:
        label = check.get("label", "unknown")
        reasons.append(
            f"{label} filledAmountIn={check.get('filledAmountIn')} < required={check.get('requiredFilledAmountIn')}"
        )
    return "；".join(reasons) or "鏈上確認未通過"


def _build_onchain_confirmation_result(
    execution_id: str,
    tx_hash: str | None = None,
    raw_keeperhub_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    將鏈上讀取結果轉成 `submit_execution_result()` 可接受的 confirmed / failed dict。

    輸入：
    - `execution_id`：本地 execution id。
    - `tx_hash`：可選交易 hash。
    - `raw_keeperhub_result`：可選 KeeperHub 原始成功回覆。

    輸出：
    - confirmed：鏈上 `filledAmountIn` 足夠，可正式套用本地訂單。
    - failed：鏈上尚無足夠證據或讀取錯誤；呼叫端可選擇不套用。
    """
    request = get_execution_request(execution_id)
    evidence = check_execution_payload_onchain_confirmation(request["payload"])
    if evidence["status"] != "confirmed":
        return {
            "status": "failed",
            "failure_reason": evidence.get("failureReason") or "onchain confirmation failed",
            "onchainEvidence": evidence,
            "raw_keeperhub_result": raw_keeperhub_result,
        }

    result: dict[str, Any] = {
        "status": "confirmed",
        "tx_hash": tx_hash,
        "onchainEvidence": evidence,
        "raw_keeperhub_result": raw_keeperhub_result,
        "notes": "KeeperHub success confirmed by SettlementRouter.filledAmountIn",
    }
    return result


def _resolve_keeperhub_final_result(execution_id: str, normalized_result: dict[str, Any]) -> dict[str, Any]:
    """
    將 KeeperHub 最終結果補上鏈上確認 fallback。

    輸入：
    - `execution_id`：本地 execution id。
    - `normalized_result`：`_extract_keeperhub_execution_result()` 的結果。

    輸出：
    - 若 KeeperHub success 沒 tx hash，但鏈上 filled amount 已達標，改回 confirmed。
    - 若 KeeperHub workflow 後段 error，但鏈上 filled amount 已達標，仍以鏈上 confirmed 為準。
    - 若鏈上無法確認，保留原本 failed 結果，避免把 workflow success/error 誤當鏈上成功。
    """
    if normalized_result.get("status") != "failed":
        return normalized_result
    confirmation_result = _build_onchain_confirmation_result(
        execution_id,
        raw_keeperhub_result=normalized_result.get("raw_receipt") or normalized_result.get("rawReceipt") or normalized_result,
    )
    if confirmation_result["status"] == "confirmed":
        return confirmation_result
    return normalized_result


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

    tx_hash = _extract_tx_hash(result)
    onchain_evidence = result.get("onchainEvidence") or result.get("onchain_evidence")
    has_confirming_evidence = isinstance(onchain_evidence, dict) and onchain_evidence.get("confirmed") is True
    if not tx_hash and not has_confirming_evidence:
        return {
            "status": "failed",
            "failureReason": "chain execution success missing tx hash",
            "rawResult": result,
        }

    return {
        "status": "confirmed",
        "txHash": tx_hash,
        "blockNumber": result.get("blockNumber") or result.get("block_number"),
        "rawReceipt": result.get("rawReceipt") or result.get("raw_receipt"),
        "onchainEvidence": onchain_evidence,
        "notes": result.get("notes") or "blockchain executor confirmed execution",
        "rawResult": result,
    }


def _extract_tx_hash(result: dict[str, Any]) -> Any:
    """
    從 execution 回報中取出鏈上交易 hash。

    輸入：
    - `result`：executor / KeeperHub 正規化前回報。

    輸出：
    - 找到時回傳 tx hash；找不到時回傳 `None`。

    用途：
    - 避免只因 KeeperHub workflow success 就把 execution 當成鏈上 confirmed。
    """
    direct = result.get("txHash") or result.get("tx_hash") or result.get("transactionHash")
    if direct:
        return direct
    for receipt_key in ("rawReceipt", "raw_receipt", "receipt"):
        receipt = result.get(receipt_key)
        if isinstance(receipt, dict):
            nested = receipt.get("txHash") or receipt.get("tx_hash") or receipt.get("transactionHash")
            if nested:
                return nested
    return None


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
            tx_hash = _extract_tx_hash(candidate)
            if not tx_hash:
                return {
                    "status": "failed",
                    "failure_reason": "KeeperHub workflow succeeded without chain tx hash",
                    "raw_receipt": candidate,
                }
            return {
                "status": "confirmed",
                "tx_hash": tx_hash,
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
