from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from scripts import orchestrator_server


FINAL_EXECUTION_STATUSES = {"confirmed", "failed"}
REQUIRED_INTENT_FIELDS = ("user", "tokenIn", "tokenOut", "amountIn", "minAmountOut", "deadline", "salt", "allowPartialFill")


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

    args = parser.parse_args()
    if args.command == "pending":
        output = get_pending_execution_requests(limit=args.limit, ready_only=args.ready_only)
    elif args.command == "get":
        output = get_execution_request(args.execution_id)
    elif args.command == "dispatch":
        output = mark_execution_dispatched(args.execution_id, json.loads(args.metadata_json))
    elif args.command == "submit":
        output = submit_execution_result(args.execution_id, json.loads(args.result_json))
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
