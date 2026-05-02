from __future__ import annotations

import hmac
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from scripts import execution_messages
from scripts import execution_reconciler
from scripts import matching_service


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
INTERNAL_TOKEN_ENV = "INTERNAL_API_TOKEN"
MATCHING_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=1)
MATCHING_JOBS: dict[str, dict[str, Any]] = {}
MATCHING_JOBS_LOCK = threading.Lock()


class RunMatchingRequest(BaseModel):
    """
    觸發後端媒合的內部請求。

    輸入：
    - `agent`：主腦來源。
    - `candidate_limit`：本輪最多提供幾筆候選買單。
    - `drain_until_empty`：是否持續處理到沒有可派發賣單。
    - `max_cycles`：drain 的安全上限。
    """

    agent: Literal["main-brain", "grok", "simulated"] = "grok"
    candidate_limit: int = Field(default=5, ge=1)
    drain_until_empty: bool = True
    max_cycles: int = Field(default=100, ge=1)


class DispatchExecutionRequest(BaseModel):
    """
    標記 execution 已送給區塊鏈端的內部請求。

    輸入：
    - `dispatch_metadata`：可選發送資訊。
    """

    dispatch_metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResultRequest(BaseModel):
    """
    區塊鏈端回報 execution 結果的內部請求。

    輸入：
    - `status`：`confirmed` 或 `failed`。
    - 其他欄位可放 `tx_hash`、`block_number`、`raw_receipt`、`failure_reason`。
    """

    model_config = ConfigDict(extra="allow")

    status: Literal["confirmed", "failed"]


class OnchainConfirmRequest(BaseModel):
    """
    以鏈上讀取結果確認 execution 的內部請求。

    輸入：
    - `tx_hash`：可選；若 KeeperHub 有提供交易 hash，可一起保存。
    - `raw_keeperhub_result`：可選；KeeperHub 原始 success 回覆，用於除錯。
    """

    tx_hash: Optional[str] = None
    raw_keeperhub_result: Optional[dict[str, Any]] = None


class KeeperHubDispatchRequest(BaseModel):
    """
    將 execution payload 送到 KeeperHub webhook 的內部請求。

    輸入：
    - `webhook_url`：可選；不填則使用後端預設 KeeperHub URL 或 `.env`。
    - `timeout_seconds`：HTTP POST 等待秒數。
    - `webhook_headers`：可選；KeeperHub 需要授權時可放額外 HTTP headers。
    - `wait_for_final_result`：可選；若為 `True`，dispatch 後會等待 KeeperHub status API 回到最終結果。
    - `poll_interval_seconds`：等待最終結果時的查詢間隔。
    - `max_wait_seconds`：等待最終結果的最長秒數。
    - `status_api_base`：可選 KeeperHub status API base URL。
    - `status_headers`：可選 KeeperHub status API 額外 headers。
    """

    webhook_url: Optional[str] = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    webhook_headers: dict[str, str] = Field(default_factory=dict)
    wait_for_final_result: bool = False
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    max_wait_seconds: float = Field(default=300.0, ge=0)
    status_api_base: Optional[str] = None
    status_headers: dict[str, str] = Field(default_factory=dict)


class KeeperHubRefreshRequest(BaseModel):
    """
    刷新 KeeperHub running executions 的內部請求。

    輸入：
    - `limit`：本次最多檢查幾筆已 dispatched 的 execution。
    - `timeout_seconds`：每次 KeeperHub status API GET 等待秒數。
    - `status_api_base`：可選；不填則使用 `.env` 或預設 KeeperHub status API。
    - `status_headers`：可選；KeeperHub status API 需要授權時可放額外 HTTP headers。
    """

    limit: int = Field(default=20, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    status_api_base: Optional[str] = None
    status_headers: dict[str, str] = Field(default_factory=dict)


class ExecutionReconcileRequest(BaseModel):
    """
    execution 自動收尾巡檢請求。

    輸入：
    - `limit`：本次最多掃描幾筆 proposed / dispatched execution。
    - `dispatch_ready`：是否自動送出欄位完整且未過期的 proposed execution。
    - `expire_invalid`：是否把缺欄位、過期、或訂單已不可處理的 proposed execution 標成 failed。
    - `refresh_dispatched`：是否查詢已送出 KeeperHub 的 execution 最終狀態。
    - `wait_for_final_result`：送出 KeeperHub 後是否等待 confirmed / failed。
    - `webhook_url`、`webhook_headers`：KeeperHub webhook 設定。
    - `timeout_seconds`：單次 HTTP request timeout 秒數。
    - `poll_interval_seconds`、`max_wait_seconds`：等待 KeeperHub 最終結果的輪詢設定。
    - `status_api_base`、`status_headers`：KeeperHub status API 設定。
    """

    limit: int = Field(default=20, ge=1)
    dispatch_ready: bool = True
    expire_invalid: bool = True
    refresh_dispatched: bool = True
    wait_for_final_result: bool = True
    webhook_url: Optional[str] = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    webhook_headers: dict[str, str] = Field(default_factory=dict)
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    max_wait_seconds: float = Field(default=300.0, ge=0)
    status_api_base: Optional[str] = None
    status_headers: dict[str, str] = Field(default_factory=dict)


class MatchingJobResponse(BaseModel):
    """
    Grok 背景媒合工作狀態。

    輸入：
    - 無；此模型只描述 API 輸出。

    輸出：
    - `jobId`：可用於輪詢的 job id。
    - `status`：`queued`、`running`、`completed` 或 `failed`。
    - `result`：完成後的媒合結果。
    - `error`：失敗時的錯誤訊息。
    """

    jobId: str
    status: Literal["queued", "running", "completed", "failed"]
    request: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    createdAt: str
    updatedAt: str


app = FastAPI(
    title="CactusNetwork Internal API",
    description="內部服務接口：媒合觸發、嚴格區塊鏈 payload 輸出、區塊鏈端結果回收。",
    version="0.1.0",
)


@app.post("/internal/matching/run", summary="觸發後端媒合")
def run_matching(payload: RunMatchingRequest, x_internal_token: str = Header(default="")) -> dict[str, Any]:
    """
    觸發後端媒合。

    輸入：
    - Header：`X-Internal-Token`。
    - JSON body：`RunMatchingRequest`。

    輸出：
    - 預設回傳 drain 結果，持續處理到沒有可派發賣單。
    - 若 `drain_until_empty=false`，只跑一輪媒合。
    """
    _require_internal_token(x_internal_token)
    if payload.drain_until_empty:
        return matching_service.run_matching_drain(
            agent=payload.agent,
            candidate_limit=payload.candidate_limit,
            max_cycles=payload.max_cycles,
        )
    return matching_service.run_matching_once(
        agent=payload.agent,
        candidate_limit=payload.candidate_limit,
    )


@app.post("/internal/matching/jobs", summary="建立背景媒合工作")
def create_matching_job(payload: RunMatchingRequest, x_internal_token: str = Header(default="")) -> dict[str, Any]:
    """
    建立背景媒合工作，避免 Grok 決策時間過長導致 HTTP / nginx timeout。

    輸入：
    - Header：`X-Internal-Token`。
    - JSON body：`RunMatchingRequest`。

    輸出：
    - 立即回傳 `jobId` 與 `status=queued`。
    - 呼叫端之後用 `GET /internal/matching/jobs/{job_id}` 查結果。
    """
    _require_internal_token(x_internal_token)
    job_id = f"matching:{uuid4().hex}"
    now = _now_iso()
    request_payload = payload.model_dump()
    job = {
        "jobId": job_id,
        "status": "queued",
        "request": request_payload,
        "result": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }
    with MATCHING_JOBS_LOCK:
        MATCHING_JOBS[job_id] = job
    MATCHING_JOB_EXECUTOR.submit(_run_matching_job, job_id, payload)
    return job


@app.get("/internal/matching/jobs/{job_id}", summary="取得背景媒合工作狀態")
def get_matching_job(job_id: str, x_internal_token: str = Header(default="")) -> dict[str, Any]:
    """
    取得背景媒合工作的目前狀態。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`job_id`。

    輸出：
    - 回傳 job 狀態；完成時包含完整 `result`。
    """
    _require_internal_token(x_internal_token)
    with MATCHING_JOBS_LOCK:
        job = MATCHING_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="matching job 不存在")
        return dict(job)


@app.get("/internal/executions/pending", summary="取得待送出的交易請求")
def get_pending_executions(
    limit: int = Query(default=20, ge=1),
    ready_only: bool = Query(default=False),
    x_internal_token: str = Header(default=""),
) -> list[dict[str, Any]]:
    """
    取得待送出的 execution requests。

    輸入：
    - Header：`X-Internal-Token`。
    - Query：`limit`、`ready_only`。

    輸出：
    - 回傳 execution request list。
    """
    _require_internal_token(x_internal_token)
    return execution_messages.get_pending_execution_requests(limit=limit, ready_only=ready_only)


@app.get("/internal/executions/{execution_id}", summary="取得單筆交易請求")
def get_execution(execution_id: str, x_internal_token: str = Header(default="")) -> dict[str, Any]:
    """
    取得單筆 execution request。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`execution_id`。

    輸出：
    - 回傳單筆 execution request。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(lambda: execution_messages.get_execution_request(execution_id))


@app.post("/internal/executions/{execution_id}/dispatch", summary="標記交易請求已送出")
def dispatch_execution(
    execution_id: str,
    payload: DispatchExecutionRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    標記 execution request 已送給區塊鏈端。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`execution_id`。
    - JSON body：`DispatchExecutionRequest`。

    輸出：
    - 回傳 dispatched 狀態。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(
        lambda: execution_messages.mark_execution_dispatched(
            execution_id,
            payload.dispatch_metadata,
        )
    )


@app.post("/internal/executions/{execution_id}/result", summary="提交區塊鏈端結果")
def submit_execution_result(
    execution_id: str,
    payload: ExecutionResultRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    接收區塊鏈端 execution 結果。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`execution_id`。
    - JSON body：
      - confirmed：`status`、`tx_hash`、`block_number`、`raw_receipt`
      - failed：`status`、`failure_reason`

    輸出：
    - 回傳中控確認結果。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(lambda: execution_messages.submit_execution_result(execution_id, payload.model_dump()))


@app.post("/internal/executions/{execution_id}/onchain/confirm", summary="讀鏈上狀態確認交易結果")
def confirm_execution_onchain(
    execution_id: str,
    payload: OnchainConfirmRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    讀取鏈上 SettlementRouter 狀態，作為 execution 最終判定。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`execution_id`。
    - JSON body：
      - `tx_hash`：可選。
      - `raw_keeperhub_result`：可選 KeeperHub 原始 success 回覆。

    輸出：
    - 鏈上 `filledAmountIn` 已達本次 payload 執行量時，回傳 confirmed 並更新本地訂單。
    - 鏈上資料尚未達標或設定缺失時，回傳 not_found，不更新本地訂單。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(
        lambda: execution_messages.confirm_execution_from_onchain(
            execution_id,
            tx_hash=payload.tx_hash,
            raw_keeperhub_result=payload.raw_keeperhub_result,
        )
    )


@app.post("/internal/executions/{execution_id}/keeperhub/dispatch", summary="送出交易請求到 KeeperHub")
def dispatch_execution_to_keeperhub(
    execution_id: str,
    payload: KeeperHubDispatchRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    將嚴格區塊鏈 payload 送到 KeeperHub webhook。

    輸入：
    - Header：`X-Internal-Token`。
    - Path：`execution_id`。
    - JSON body：`KeeperHubDispatchRequest`。

    輸出：
    - 回傳 KeeperHub 回覆與本地 execution 狀態。
    - 若 KeeperHub 回覆 confirmed / failed，後端會同步套用該結果。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(
        lambda: execution_messages.send_execution_to_keeperhub(
            execution_id,
            webhook_url=payload.webhook_url,
            timeout_seconds=payload.timeout_seconds,
            webhook_headers=payload.webhook_headers,
            wait_for_final_result=payload.wait_for_final_result,
            poll_interval_seconds=payload.poll_interval_seconds,
            max_wait_seconds=payload.max_wait_seconds,
            status_api_base=payload.status_api_base,
            status_headers=payload.status_headers,
        )
    )


@app.post("/internal/executions/keeperhub/refresh", summary="刷新 KeeperHub 執行結果")
def refresh_keeperhub_executions(
    payload: KeeperHubRefreshRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    檢查已送到 KeeperHub 的 running executions，拿到最終結果後自動收尾。

    輸入：
    - Header：`X-Internal-Token`。
    - JSON body：`KeeperHubRefreshRequest`。

    輸出：
    - `waiting`：仍在 KeeperHub running / pending。
    - `finalized`：本次已 confirmed / failed 的 execution。
    - `skipped`：缺少 KeeperHub execution id 或重複資料。
    - `errors`：KeeperHub status API 讀取失敗。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(
        lambda: execution_messages.refresh_keeperhub_execution_results(
            limit=payload.limit,
            timeout_seconds=payload.timeout_seconds,
            status_api_base=payload.status_api_base,
            status_headers=payload.status_headers,
        )
    )


@app.post("/internal/executions/reconcile", summary="自動收尾 executions")
def reconcile_executions(
    payload: ExecutionReconcileRequest,
    x_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    執行一次 execution 自動收尾巡檢。

    輸入：
    - Header：`X-Internal-Token`。
    - JSON body：`ExecutionReconcileRequest`。

    輸出：
    - `refreshBefore` / `refreshAfter`：dispatched execution 的 KeeperHub 狀態刷新結果。
    - `expired`：本次因缺欄位、deadline 過期或訂單不可處理而標成 failed 的 execution。
    - `dispatched`：本次送 KeeperHub 的 execution。
    - `skipped`：本次略過的 execution。
    - `errors`：派送或清理時遇到的錯誤。
    """
    _require_internal_token(x_internal_token)
    return _call_or_404(
        lambda: execution_reconciler.reconcile_keeperhub_executions(
            limit=payload.limit,
            dispatch_ready=payload.dispatch_ready,
            expire_invalid=payload.expire_invalid,
            refresh_dispatched=payload.refresh_dispatched,
            wait_for_final_result=payload.wait_for_final_result,
            webhook_url=payload.webhook_url,
            timeout_seconds=payload.timeout_seconds,
            webhook_headers=payload.webhook_headers,
            poll_interval_seconds=payload.poll_interval_seconds,
            max_wait_seconds=payload.max_wait_seconds,
            status_api_base=payload.status_api_base,
            status_headers=payload.status_headers,
        )
    )


def _require_internal_token(provided_token: str) -> None:
    """
    驗證內部 API token。

    輸入：
    - `provided_token`：HTTP header `X-Internal-Token`。

    輸出：
    - 驗證成功時無回傳。

    錯誤：
    - 未設定 `INTERNAL_API_TOKEN` 時回傳 500。
    - token 錯誤時回傳 401。
    """
    _load_env()
    expected_token = os.getenv(INTERNAL_TOKEN_ENV)
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_API_TOKEN 尚未設定",
        )
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="內部 token 錯誤")


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


def _call_or_404(callback):
    """
    將內部 ValueError 轉成 HTTP error。

    輸入：
    - `callback`：要執行的函式。

    輸出：
    - 回傳 callback 結果。
    """
    try:
        return callback()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _run_matching_job(job_id: str, payload: RunMatchingRequest) -> None:
    """
    實際執行背景媒合 job。

    輸入：
    - `job_id`：背景工作 id。
    - `payload`：媒合請求內容。

    輸出：
    - 無直接回傳；結果寫回 `MATCHING_JOBS`。
    """
    _update_matching_job(job_id, status_value="running")
    try:
        if payload.drain_until_empty:
            result = matching_service.run_matching_drain(
                agent=payload.agent,
                candidate_limit=payload.candidate_limit,
                max_cycles=payload.max_cycles,
            )
        else:
            result = matching_service.run_matching_once(
                agent=payload.agent,
                candidate_limit=payload.candidate_limit,
            )
        _update_matching_job(job_id, status_value="completed", result=result)
    except Exception as exc:
        _update_matching_job(
            job_id,
            status_value="failed",
            error=f"{exc.__class__.__name__}: {exc}",
            result={"traceback": traceback.format_exc(limit=8)},
        )


def _update_matching_job(
    job_id: str,
    *,
    status_value: Literal["queued", "running", "completed", "failed"],
    result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """
    更新背景媒合 job 狀態。

    輸入：
    - `job_id`：背景工作 id。
    - `status_value`：新的工作狀態。
    - `result`：完成或失敗時的結果。
    - `error`：失敗訊息。

    輸出：
    - 無。
    """
    with MATCHING_JOBS_LOCK:
        job = MATCHING_JOBS.get(job_id)
        if job is None:
            return
        job["status"] = status_value
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        job["updatedAt"] = _now_iso()


def _now_iso() -> str:
    """
    取得目前 UTC ISO 時間字串。

    輸入：
    - 無。

    輸出：
    - 回傳 ISO 8601 字串。
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
