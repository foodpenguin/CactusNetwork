from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from scripts import blockchain_sync
from scripts import grok_minimal
from scripts import orchestrator_server


AgentDecide = Callable[[dict[str, Any], Optional[dict[str, Any]]], dict[str, Any]]
PROJECT_DIR = Path(__file__).resolve().parent.parent
DECISION_FLOW_DOC = PROJECT_DIR / "CactusNetwork_AI_Agent_決策流程.md"
ONCHAIN_GUIDE_DOC = PROJECT_DIR / "CactusNetwork_鏈上狀態同步與讀取技術指南.md"
ALLOWED_DECISION_STATUSES = {
    "proposed_execution",
    "request_external_contract_data",
    "rejected",
    "invalid",
}


def run_agent_cycle(agent_decide: AgentDecide, candidate_limit: int = 5) -> dict[str, Any]:
    """
    執行一輪 agents 撮合流程。

    輸入：
    - `agent_decide`：agent 決策函式。
      - 第一次呼叫時輸入 `task` 與 `external_context=None`。
      - 若 agent 要求外部合約資料，第二次呼叫會帶入 `external_context`。
    - `candidate_limit`：中控準備任務時最多提供幾筆本地候選買單。

    輸出：
    - 回傳本輪 runner 結果，包含 prepare、agent decision、外部查詢與 apply 結果。

    副作用：
    - 呼叫中控函式庫準備 task。
    - 必要時呼叫區塊鏈函式庫寫入 `external_contracts.db` 與 `onchain_state.db`。
    - 對最終 agent 決策呼叫中控 `apply_agent_decision()`。
    """
    prepared = orchestrator_server.prepare_agent_task(candidate_limit=candidate_limit)
    if prepared["status"] != "prepared":
        return {
            "status": "no_task",
            "prepared": prepared,
            "agentDecision": None,
            "externalRequest": None,
            "externalContext": None,
            "applyResult": None,
        }

    task = prepared["task"]
    first_decision = agent_decide(task, None)

    if first_decision.get("decisionStatus") != "request_external_contract_data":
        apply_result = orchestrator_server.apply_agent_decision(first_decision)
        return {
            "status": _runner_status_from_apply_result(apply_result),
            "prepared": prepared,
            "agentDecision": first_decision,
            "externalRequest": None,
            "externalContext": None,
            "applyResult": apply_result,
        }

    request_result = orchestrator_server.apply_agent_decision(first_decision)
    external_query = dict(first_decision.get("externalQuery") or {})
    external_query.setdefault("taskId", task["taskId"])
    external_query.setdefault("reason", first_decision.get("reason") or first_decision.get("failureReason"))
    external_request = blockchain_sync.request_external_contract_data(task["sellOrder"], external_query)
    external_context = blockchain_sync.get_external_contract_context(external_request["queryId"])
    final_decision = agent_decide(task, external_context)

    if final_decision.get("decisionStatus") == "request_external_contract_data":
        return {
            "status": "waiting_for_final_agent_decision",
            "prepared": prepared,
            "agentDecision": first_decision,
            "externalRequestRecord": request_result,
            "externalRequest": external_request,
            "externalContext": external_context,
            "finalAgentDecision": final_decision,
            "applyResult": None,
        }

    apply_result = orchestrator_server.apply_agent_decision(final_decision)
    return {
        "status": _runner_status_from_apply_result(apply_result, after_external=True),
        "prepared": prepared,
        "agentDecision": first_decision,
        "externalRequestRecord": request_result,
        "externalRequest": external_request,
        "externalContext": external_context,
        "finalAgentDecision": final_decision,
        "applyResult": apply_result,
    }


def _runner_status_from_apply_result(apply_result: dict[str, Any], after_external: bool = False) -> str:
    """
    根據中控 apply 結果轉換 runner 狀態。

    輸入：
    - `apply_result`：`orchestrator_server.apply_agent_decision()` 回傳值。
    - `after_external`：是否是在外部資料回來後套用最終決策。

    輸出：
    - 回傳 runner 層的人類可讀狀態。
    """
    if apply_result.get("decisionStatus") == "proposed_execution":
        return "execution_proposed_after_external_context" if after_external else "execution_proposed"
    return "completed_after_external_context" if after_external else "completed"


def grok_agent_decide(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    使用 Grok 作為主腦產生 agent decision。

    輸入：
    - `task`：中控準備出的本輪任務。
    - `external_context`：外部合約資料；第一次決策時為 `None`。

    輸出：
    - 回傳 runner / orchestrator 可套用的 decision dict。

    副作用：
    - 讀取 `memory/*.md` 與兩份設計文件。
    - 呼叫 Grok API。

    格式規則：
    - Grok 只能回傳單一 JSON object。
    - 若文件內容與本系統 decision I/O 衝突，以本函式 prompt 內的格式化輸出規格為準。
    """
    memory = grok_minimal.load_agent_memory()
    prompt = build_grok_decision_prompt(task, external_context)
    raw_output = grok_minimal.ask_grok_with_memory(memory=memory, task=prompt)
    decision = parse_grok_decision(raw_output)
    return validate_grok_decision(decision, task)


def build_grok_decision_prompt(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> str:
    """
    建立 Grok 主腦決策 prompt。

    輸入：
    - `task`：中控任務。
    - `external_context`：外部合約上下文或 `None`。

    輸出：
    - 回傳完整 prompt 字串。
    """
    decision_flow = _read_text_if_exists(DECISION_FLOW_DOC)
    onchain_guide = _read_text_if_exists(ONCHAIN_GUIDE_DOC)
    payload = {
        "task": task,
        "externalContext": external_context,
    }
    return f"""
你是 CactusNetwork 的主腦 agents 決策層。

你需要參考下列文件語意做決策，但輸出格式必須完全以「本 prompt 的格式化主腦輸出」為準。
如果文件中的範例輸出與本 prompt 規格不同，以本 prompt 規格為準。

決策原則：
1. 優先檢查內部 OTC：candidateBuyOrders 是否可以和 sellOrder 撮合。
2. 若內部 OTC 可行，輸出 proposed_execution。
3. 內部 OTC 可行時，請用多筆 candidateBuyOrders 拆單，直到達到 sellOrder.remainingAmount、候選買單不足、或 sellOrder.maxSplits 上限。
4. 每筆 match 的 filledAmount 不得超過該買單 remainingAmount，也不得讓 matches 總量超過 sellOrder.remainingAmount。
5. 若內部 OTC 只能部分成交，仍可輸出 proposed_execution；剩餘量會等 executor confirmed 後回到隊列。
6. 若內部 OTC 完全沒有可用候選且 externalContext 是 null，輸出 request_external_contract_data。
7. 若 externalContext 已提供且候選資料內有 reads.Calldata，輸出 actionType=0 的 proposed_execution。
8. 若 externalContext 已提供，但你仍不能安全產生成交 payload，輸出 rejected。
9. 不要回傳 matched。成交必須等 executor / keeper 回覆 confirmed 後才由中控記錄。
10. 不要假造錢包地址、intent、signature、鏈上資料。不知道就留 null；後端會用 DB 訂單資料補齊 intent/signature。
11. 回覆只能是單一 JSON object，不要 Markdown，不要解釋文字。

允許的 decisionStatus：
- proposed_execution
- request_external_contract_data
- rejected
- invalid

內部 OTC proposed_execution 格式：
{{
  "taskId": "{task.get("taskId")}",
  "decisionStatus": "proposed_execution",
  "sellOrderId": {task.get("sellOrder", {}).get("id")},
  "matches": [
    {{
      "buyOrderId": 1,
      "filledAmount": 1,
      "unitPriceUsdc": 2900
    }}
  ],
  "executionPayload": {{
    "intentA": {{
      "intent": null,
      "signature": null
    }},
    "actionType": 1,
    "executeAmountIn": "1",
    "routeDetails": {{
      "Calldata": null,
      "matchedIntentB": {{
        "intent": null,
        "signature": null,
        "executeAmountInB": "2900"
      }},
      "treasuryAmountOut": null
    }}
  }},
  "decisionChain": [],
  "agentNotes": "簡短說明"
}}

外部 DEX proposed_execution 格式：
{{
  "taskId": "{task.get("taskId")}",
  "decisionStatus": "proposed_execution",
  "sellOrderId": {task.get("sellOrder", {}).get("id")},
  "matches": [],
  "executionPayload": {{
    "intentA": {{
      "intent": null,
      "signature": null
    }},
    "actionType": 0,
    "executeAmountIn": "使用 externalContext.candidates[].candidate.reads.amountIn 或 sellOrder.intentJson.amountIn",
    "routeDetails": {{
      "Calldata": "使用 externalContext.candidates[].candidate.reads.Calldata",
      "matchedIntentB": null,
      "treasuryAmountOut": null
    }}
  }},
  "decisionChain": [],
  "agentNotes": "簡短說明"
}}

request_external_contract_data 格式：
{{
  "taskId": "{task.get("taskId")}",
  "decisionStatus": "request_external_contract_data",
  "sellOrderId": {task.get("sellOrder", {}).get("id")},
  "reason": "為什麼需要外部合約資料",
  "externalQuery": {{
    "sourceOrderType": "sell",
    "asset": "{task.get("sellOrder", {}).get("asset")}",
    "amount": {json.dumps(task.get("sellOrder", {}).get("remainingAmount"), ensure_ascii=False)},
    "minUnitPriceUsdc": {json.dumps(task.get("sellOrder", {}).get("minUnitPriceUsdc"), ensure_ascii=False)},
    "syncTargets": []
  }},
  "decisionChain": []
}}

rejected / invalid 格式：
{{
  "taskId": "{task.get("taskId")}",
  "decisionStatus": "rejected",
  "sellOrderId": {task.get("sellOrder", {}).get("id")},
  "failureReason": "拒絕或標記無效的原因",
  "decisionChain": [],
  "agentNotes": "簡短說明"
}}

決策流程文件：
```markdown
{decision_flow}
```

鏈上同步文件：
```markdown
{onchain_guide}
```

本輪輸入資料：
```json
{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}
```
""".strip()


def parse_grok_decision(raw_output: str) -> dict[str, Any]:
    """
    將 Grok 文字輸出解析成 decision dict。

    輸入：
    - `raw_output`：Grok 原始文字。

    輸出：
    - 回傳 JSON object dict。

    錯誤：
    - 無法解析成 JSON object 時拋出 `ValueError`。
    """
    text = raw_output.strip()
    candidates = [text]

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("Grok decision 必須是 JSON object")

    raise ValueError(f"無法解析 Grok decision JSON：{last_error}") from last_error


def validate_grok_decision(decision: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """
    驗證並補齊 Grok decision 的必要欄位。

    輸入：
    - `decision`：Grok 回傳並解析後的 dict。
    - `task`：原始中控任務，用於補齊 `taskId` 與 `sellOrderId`。

    輸出：
    - 回傳可交給 `orchestrator_server.apply_agent_decision()` 的 dict。

    錯誤：
    - decisionStatus 不合法或缺必要欄位時拋出 `ValueError`。
    """
    status = str(decision.get("decisionStatus", "")).strip()
    if status not in ALLOWED_DECISION_STATUSES:
        raise ValueError("Grok decisionStatus 必須是 proposed_execution、request_external_contract_data、rejected 或 invalid")

    normalized = dict(decision)
    normalized.setdefault("taskId", task["taskId"])
    normalized.setdefault("sellOrderId", task["sellOrder"]["id"])

    if int(normalized["sellOrderId"]) != int(task["sellOrder"]["id"]):
        raise ValueError("Grok decision 的 sellOrderId 必須等於本輪 task 的 sellOrder.id")

    if status == "proposed_execution":
        if not isinstance(normalized.get("executionPayload"), dict):
            if isinstance(normalized.get("matches"), list) and normalized["matches"]:
                normalized["executionPayload"] = {}
            else:
                raise ValueError("proposed_execution 必須包含 executionPayload 或非空 matches")
        if _is_external_dex_payload(normalized["executionPayload"]):
            normalized.setdefault("matches", [])
        elif not isinstance(normalized.get("matches"), list) or not normalized["matches"]:
            raise ValueError("內部 OTC proposed_execution 必須包含非空 matches")
        normalized.setdefault("agentNotes", "Grok 主腦提出成交單")
    elif status == "request_external_contract_data":
        external_query = normalized.setdefault("externalQuery", {})
        if not isinstance(external_query, dict):
            raise ValueError("request_external_contract_data 的 externalQuery 必須是 object")
        external_query.setdefault("sourceOrderType", "sell")
        external_query.setdefault("asset", task["sellOrder"].get("asset"))
        external_query.setdefault("amount", task["sellOrder"].get("remainingAmount"))
        external_query.setdefault("minUnitPriceUsdc", task["sellOrder"].get("minUnitPriceUsdc"))
        external_query.setdefault("syncTargets", [])
        if not external_query["syncTargets"]:
            external_query["syncTargets"] = _build_uniswap_sync_targets_from_task_sell_order(task["sellOrder"])
    else:
        normalized.setdefault("failureReason", "Grok 主腦未提供原因")

    normalized.setdefault("decisionChain", [])
    return normalized


def _build_uniswap_sync_targets_from_task_sell_order(sell_order: dict[str, Any]) -> list[dict[str, Any]]:
    """
    從本輪賣單 payload 補齊外部 Uniswap V3 查詢目標。

    輸入：
    - `sell_order`：`prepare_agent_task()` 交給 Grok 的 `sellOrder` payload。

    輸出：
    - 若賣單 `intentJson` 有 `user`、`tokenIn`、`tokenOut`、`amountIn`，回傳一筆 Uniswap target。
    - 若欄位不足，回傳空 list，讓外部查詢明確呈現 `no_targets`。

    用途：
    - Grok 負責判斷是否需要外部資料。
    - 後端負責把已簽署 intent 轉成穩定的 Uniswap API 查詢格式，避免 Grok 漏填 `syncTargets`。
    """
    raw_intent = sell_order.get("intentJson") if isinstance(sell_order.get("intentJson"), dict) else {}
    user = raw_intent.get("user")
    token_in = raw_intent.get("tokenIn")
    token_out = raw_intent.get("tokenOut")
    amount_in = raw_intent.get("amountIn")
    if not all([user, token_in, token_out, amount_in]):
        return []

    target: dict[str, Any] = {
        "intentId": f"sell-order-{sell_order['id']}-uniswap-v3",
        "user": user,
        "swapper": raw_intent.get("swapper") or user,
        "recipient": raw_intent.get("recipient") or user,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amount": str(amount_in),
        "amountIn": str(amount_in),
        "buildCalldata": True,
        "buildApprovalCheck": True,
        "protocols": ["V3"],
    }
    for source_key, target_key in (
        ("chainId", "chainId"),
        ("tokenInChainId", "tokenInChainId"),
        ("tokenOutChainId", "tokenOutChainId"),
        ("fee", "fee"),
        ("priceLimit", "priceLimit"),
        ("sqrtPriceLimitX96", "sqrtPriceLimitX96"),
        ("slippageTolerance", "slippageTolerance"),
        ("routingPreference", "routingPreference"),
        ("urgency", "urgency"),
    ):
        if raw_intent.get(source_key) is not None:
            target[target_key] = raw_intent[source_key]
    return [target]


def _is_external_dex_payload(payload: dict[str, Any]) -> bool:
    """
    判斷 Grok proposed_execution 是否為外部 DEX payload。

    輸入：
    - `payload`：Grok 回傳的 executionPayload。

    輸出：
    - `actionType == 0` 且 `routeDetails.Calldata` 存在時回傳 `True`。
    """
    route_details = payload.get("routeDetails") if isinstance(payload.get("routeDetails"), dict) else {}
    return payload.get("actionType") == 0 and bool(route_details.get("Calldata"))


def simulated_agent_decide(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    提供本地手動測試用的模擬 agent。

    輸入：
    - `task`：中控準備給 agent 的任務。
    - `external_context`：外部合約資料；第一次決策時為 `None`。

    輸出：
    - 回傳一個保守的 agent decision。

    注意：
    - 此函式不代表真實交易策略，只用來驗證 runner 流程可跑通。
    """
    sell_order = task["sellOrder"]
    if external_context is None and not task["candidateBuyOrders"]:
        return {
            "taskId": task["taskId"],
            "decisionStatus": "request_external_contract_data",
            "sellOrderId": sell_order["id"],
            "reason": "本地沒有候選買單，模擬 agent 要求外部合約資料",
            "externalQuery": {
                "sourceOrderType": "sell",
                "syncTargets": [],
            },
        }

    return {
        "taskId": task["taskId"],
        "decisionStatus": "rejected",
        "sellOrderId": sell_order["id"],
        "failureReason": "模擬 agent 不執行真實成交，只驗證 runner 流程",
    }


def run_cli() -> None:
    """
    本地命令列入口。

    輸入：
    - `--candidate-limit`：本輪最多提供幾筆本地候選買單給 agent。

    輸出：
    - 將 runner 結果以 JSON 印到 stdout。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork agents runner")
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--agent", choices=["simulated", "main-brain", "grok"], default="grok")
    args = parser.parse_args()

    if args.agent == "grok":
        agent_decide = grok_agent_decide
    elif args.agent == "main-brain":
        from scripts import main_brain

        agent_decide = main_brain.decide
    else:
        agent_decide = simulated_agent_decide

    result = run_agent_cycle(agent_decide, candidate_limit=args.candidate_limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _read_text_if_exists(path: Path) -> str:
    """
    讀取文字檔，缺檔時回傳空字串。

    輸入：
    - `path`：文字檔路徑。

    輸出：
    - 回傳檔案內容或空字串。
    """
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    run_cli()
