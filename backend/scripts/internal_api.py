from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from scripts import execution_messages
from scripts import matching_service


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
INTERNAL_TOKEN_ENV = "INTERNAL_API_TOKEN"


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
