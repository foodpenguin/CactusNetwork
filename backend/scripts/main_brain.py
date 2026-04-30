from __future__ import annotations

from typing import Any, Optional


REQUIRED_INTENT_FIELDS = ["user", "tokenIn", "tokenOut", "amountIn", "minAmountOut", "deadline", "salt", "allowPartialFill"]


def decide(task: dict[str, Any], external_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    CactusNetwork 第一版主腦決策函式。

    輸入：
    - `task`：`orchestrator_server.prepare_agent_task()` 產生的任務。
    - `external_context`：runner 查完外部合約後回傳的上下文；第一次決策時為 `None`。

    輸出：
    - 回傳符合中控 decision I/O 的 dict：
      - `proposed_execution`
      - `request_external_contract_data`
      - `rejected`

    原則：
    - 先看內部 OTC 候選。
    - 沒有內部候選才要求外部合約資料。
    - 不改 DB、不送交易、不確認成交。
    - 內部 decision 的 `executionPayload` 完全遵照區塊鏈端格式：
      `intentA`、`actionType`、`executeAmountIn`、`routeDetails`。
    - `routeDetails` 固定包含 `Calldata`、`matchedIntentB`、`treasuryAmountOut`。
    - 對外 execution 查詢接口會將它命名為 `payload`；真正送鏈時只送該 dict。
    - 主腦不知道的錢包與簽名欄位留 `None`，不額外包 debug envelope。
    """
    sell_order = task["sellOrder"]
    candidates = task.get("candidateBuyOrders") or []
    decision_chain: list[dict[str, Any]] = []

    data_summary = _data_collector_agent(task, external_context)
    decision_chain.append(data_summary)

    internal_evaluation = _internal_otc_agent(sell_order, candidates)
    decision_chain.append(internal_evaluation)

    scoring = _strategy_scoring_agent(internal_evaluation, external_context)
    decision_chain.append(scoring)

    if internal_evaluation["status"] == "candidate_selected":
        decision_chain.append(
            _chain_step(
                "payload_builder_agent",
                "輸出",
                "build_local_otc_payload",
                "策略 B 內部 OTC 分數最高，輸出 proposed_execution 與嚴格區塊鏈 payload",
            )
        )
        return _build_local_otc_execution(
            task,
            sell_order,
            internal_evaluation["details"]["selectedCandidate"],
            decision_chain,
        )

    if external_context is None:
        external_request = _external_request_agent(sell_order)
        decision_chain.append(external_request)
        return _build_external_request(task, sell_order, decision_chain)

    external_evaluation = _external_context_agent(external_context)
    decision_chain.append(external_evaluation)

    external_candidate = external_evaluation["details"].get("selectedCandidate")
    if external_candidate is not None:
        decision_chain.append(
            _chain_step(
                "payload_builder_agent",
                "輸出",
                "build_external_dex_payload",
                "外部 Uniswap V3 報價與 Calldata 可用，輸出 actionType=0 的 DEX payload",
            )
        )
        return _build_external_dex_execution(task, sell_order, external_candidate, decision_chain)

    if external_evaluation["details"]["validCandidateCount"] > 0:
        return _build_rejected_decision(
            task,
            sell_order,
            "外部合約資料已取得，但沒有可送 router.call(calldata) 的 Calldata，先不提出成交單",
            decision_chain,
        )

    return _build_rejected_decision(task, sell_order, "內部無候選，外部合約資料也沒有可用候選", decision_chain)


def _data_collector_agent(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    資料蒐集代理：整理主腦本輪可見資料。

    輸入：
    - `task`：runner 任務。
    - `external_context`：外部合約上下文或 `None`。

    輸出：
    - 回傳 decision chain step。
    """
    sell_order = task["sellOrder"]
    candidates = task.get("candidateBuyOrders") or []
    return _chain_step(
        "data_collector_agent",
        "階段一：蒐集資料",
        "data_collected",
        "讀取本地賣單、本地候選買單與外部合約上下文狀態",
        {
            "sellOrderId": sell_order["id"],
            "asset": sell_order["asset"],
            "candidateBuyOrderCount": len(candidates),
            "hasExternalContext": external_context is not None,
            "sellHasIntent": bool(sell_order.get("hasIntent")),
            "sellHasSignature": bool(sell_order.get("hasSignature")),
        },
    )


def _internal_otc_agent(sell_order: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """
    內部 OTC 評估代理：依文件策略 B 評估本地買賣是否可撮合。

    輸入：
    - `sell_order`：目前賣單。
    - `candidates`：本地候選買單。

    輸出：
    - 回傳 decision chain step；若可撮合，包含 `selectedCandidate`。
    """
    if not candidates:
        return _chain_step(
            "internal_otc_agent",
            "階段二：策略 B 暗池 OTC",
            "no_internal_candidate",
            "本地沒有符合中控基本規則的買單候選",
            {"candidateBuyOrderCount": 0},
        )

    selected = _select_best_internal_candidate(candidates)
    unit_price = float(sell_order["minUnitPriceUsdc"])
    filled_amount = min(float(sell_order["remainingAmount"]), float(selected["remainingAmount"]))
    price_overlap = float(selected["maxUnitPriceUsdc"]) >= unit_price
    asset_matches = selected["asset"] == sell_order["asset"]
    status = "candidate_selected" if price_overlap and asset_matches and filled_amount > 0 else "candidate_rejected"
    return _chain_step(
        "internal_otc_agent",
        "階段二：策略 B 暗池 OTC",
        status,
        "依價格重疊與資產配對選出最高價本地買單候選",
        {
            "selectedBuyOrderId": selected["id"],
            "assetMatches": asset_matches,
            "priceOverlap": price_overlap,
            "unitPriceUsdc": unit_price,
            "filledAmount": filled_amount,
            "selectedCandidate": selected,
        },
    )


def _strategy_scoring_agent(
    internal_evaluation: dict[str, Any],
    external_context: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    策略評分代理：依文件優先順序評估策略 B/C/A。

    輸入：
    - `internal_evaluation`：內部 OTC 評估結果。
    - `external_context`：外部合約上下文或 `None`。

    輸出：
    - 回傳 decision chain step。
    """
    internal_ready = internal_evaluation["status"] == "candidate_selected"
    external_known = external_context is not None
    external_valid_count = len(_valid_external_candidates(external_context)) if external_context else 0
    strategy_scores = {
        "strategyB_OTC": 100 if internal_ready else 0,
        "strategyC_Treasury": 40 if external_valid_count > 0 else 0,
        "strategyA_DEX": 10 if external_known else 0,
    }
    if internal_ready:
        selected_strategy = "strategyB_OTC"
    elif not external_known:
        selected_strategy = "need_external_contract_data"
    elif external_valid_count > 0:
        selected_strategy = "strategyA_DEX"
    else:
        selected_strategy = "reject_no_candidate"

    return _chain_step(
        "strategy_scoring_agent",
        "階段三：做出決策與輸出",
        selected_strategy,
        "依文件優先順序評分：策略 B OTC > 策略 C 國庫 > 策略 A DEX",
        {
            "strategyScores": strategy_scores,
            "externalValidCandidateCount": external_valid_count,
        },
    )


def _external_request_agent(sell_order: dict[str, Any]) -> dict[str, Any]:
    """
    外部資料代理：在本地不足時要求 runner 查外部合約。

    輸入：
    - `sell_order`：目前賣單。

    輸出：
    - 回傳 decision chain step。
    """
    return _chain_step(
        "external_contract_request_agent",
        "階段一：蒐集鏈上真實狀態",
        "request_external_contract_data",
        "本地 OTC 不足，要求 runner 查鏈上 filledAmountIn、Vault Balance 與 Treasury 狀態",
        {
            "asset": sell_order["asset"],
            "amount": sell_order["remainingAmount"],
            "minUnitPriceUsdc": sell_order["minUnitPriceUsdc"],
        },
    )


def _external_context_agent(external_context: dict[str, Any]) -> dict[str, Any]:
    """
    外部上下文代理：整理外部合約候選狀態。

    輸入：
    - `external_context`：外部合約上下文。

    輸出：
    - 回傳 decision chain step。
    """
    valid_candidates = _valid_external_candidates(external_context)
    selected = _select_external_candidate_with_calldata(valid_candidates)
    return _chain_step(
        "external_context_agent",
        "階段二：外部合約評估",
        "external_context_evaluated",
        "整理外部合約候選；有 Calldata 時可產生 actionType=0 的外部 DEX payload",
        {
            "candidateCount": len(external_context.get("candidates", [])),
            "validCandidateCount": len(valid_candidates),
            "selectedTargetId": selected.get("targetId") if selected else None,
            "selectedCandidate": selected,
        },
    )


def _chain_step(
    agent_name: str,
    stage: str,
    status: str,
    summary: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    建立主腦決策鏈紀錄。

    輸入：
    - `agent_name`：本步驟代理名稱。
    - `stage`：對應決策流程文件階段。
    - `status`：本步驟狀態。
    - `summary`：人類可讀摘要。
    - `details`：可選細節。

    輸出：
    - 回傳可寫入 decision JSON 的 chain step。
    """
    return {
        "agentName": agent_name,
        "stage": stage,
        "status": status,
        "summary": summary,
        "details": details or {},
    }


def _select_best_internal_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """
    選出最適合的本地買單候選。

    輸入：
    - `candidates`：中控提供的 candidateBuyOrders。

    輸出：
    - 回傳最高 `maxUnitPriceUsdc` 的候選；同價時使用較早建立者。
    """
    return sorted(
        candidates,
        key=lambda item: (
            -float(item["maxUnitPriceUsdc"]),
            str(item.get("createdAt") or ""),
            int(item["id"]),
        ),
    )[0]


def _build_local_otc_execution(
    task: dict[str, Any],
    sell_order: dict[str, Any],
    buy_order: dict[str, Any],
    decision_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    建立內部 OTC 成交提案。

    輸入：
    - `task`：runner 任務。
    - `sell_order`：目前主賣單。
    - `buy_order`：被選中的本地買單。

    輸出：
    - 回傳 `decisionStatus = proposed_execution`，並包含嚴格區塊鏈 payload。
    """
    filled_amount = min(float(sell_order["remainingAmount"]), float(buy_order["remainingAmount"]))
    unit_price = float(sell_order["minUnitPriceUsdc"])
    buyer_amount = filled_amount * unit_price

    return {
        "taskId": task["taskId"],
        "decisionStatus": "proposed_execution",
        "sellOrderId": sell_order["id"],
        "matches": [
            {
                "buyOrderId": buy_order["id"],
                "filledAmount": filled_amount,
                "unitPriceUsdc": unit_price,
            }
        ],
        "executionPayload": _build_keeper_payload(
            sell_order=sell_order,
            buy_order=buy_order,
            execute_amount_in=filled_amount,
            execute_amount_in_b=buyer_amount,
            unit_price_usdc=unit_price,
        ),
        "decisionChain": decision_chain,
        "agentNotes": "主腦：內部 OTC 候選可滿足賣單底價，輸出文件格式成交提案；錢包與簽名欄位等待前端補齊",
    }


def _build_keeper_payload(
    *,
    sell_order: dict[str, Any],
    buy_order: dict[str, Any],
    execute_amount_in: float,
    execute_amount_in_b: float,
    unit_price_usdc: float,
) -> dict[str, Any]:
    """
    依文件格式建立嚴格區塊鏈 payload。

    輸入：
    - `sell_order`：賣方本地訂單。
    - `buy_order`：買方本地訂單。
    - `execute_amount_in`：本次從賣方 intentA 扣除的資產數量。
    - `execute_amount_in_b`：本次從買方 matchedIntentB 扣除的 USDC 數量。
    - `unit_price_usdc`：本次提案單價。

    輸出：
    - 回傳 execution payload dict。

    注意：
    - 若本地訂單尚未有 MetaMask 簽名與鏈上 intent 欄位，未知欄位直接留 `None`。
    - 此函式不輸出 `payloadStatus`、`missingFields`、`AI_to_Backend_Payload` 等內部 envelope。
    """
    intent_a = _intent_from_order(
        sell_order,
        fallback_amount_in=sell_order["remainingAmount"],
        fallback_min_amount_out=float(sell_order["minUnitPriceUsdc"]) * float(sell_order["remainingAmount"]),
    )
    intent_b = _intent_from_order(
        buy_order,
        fallback_amount_in=float(buy_order["remainingAmount"]) * unit_price_usdc,
        fallback_min_amount_out=buy_order["remainingAmount"],
    )
    signature_a = sell_order.get("signature")
    signature_b = buy_order.get("signature")
    return {
        "intentA": {
            "intent": intent_a,
            "signature": signature_a,
        },
        "actionType": 1,
        "executeAmountIn": _number_to_string(execute_amount_in),
        "routeDetails": {
            "Calldata": _calldata_from_orders(sell_order, buy_order),
            "matchedIntentB": {
                "intent": intent_b,
                "signature": signature_b,
                "executeAmountInB": _number_to_string(execute_amount_in_b),
            },
            "treasuryAmountOut": None,
        },
    }


def _build_external_dex_execution(
    task: dict[str, Any],
    sell_order: dict[str, Any],
    external_candidate: dict[str, Any],
    decision_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    建立外部 DEX 成交提案。

    輸入：
    - `task`：runner 任務。
    - `sell_order`：目前主賣單。
    - `external_candidate`：`blockchain_sync` 整理後的外部候選，需包含 `reads.Calldata`。
    - `decision_chain`：本輪主腦決策鏈。

    輸出：
    - 回傳 `decisionStatus = proposed_execution`。
    - `executionPayload` 完全遵照區塊鏈端格式，且 `actionType = 0`。
    """
    reads = external_candidate.get("reads") or {}
    execute_amount_in = reads.get("amountIn") or _intent_from_order(sell_order, sell_order["remainingAmount"], 0)["amountIn"]
    calldata = reads["Calldata"]

    return {
        "taskId": task["taskId"],
        "decisionStatus": "proposed_execution",
        "sellOrderId": sell_order["id"],
        "matches": [],
        "externalTargetId": external_candidate.get("targetId"),
        "executionPayload": {
            "intentA": {
                "intent": _intent_from_order(
                    sell_order,
                    fallback_amount_in=sell_order["remainingAmount"],
                    fallback_min_amount_out=float(sell_order["minUnitPriceUsdc"]) * float(sell_order["remainingAmount"]),
                ),
                "signature": sell_order.get("signature"),
            },
            "actionType": 0,
            "executeAmountIn": str(execute_amount_in),
            "routeDetails": {
                "Calldata": calldata,
                "matchedIntentB": None,
                "treasuryAmountOut": None,
            },
        },
        "decisionChain": decision_chain,
        "agentNotes": "主腦：內部 OTC 無可用候選，使用外部 Uniswap V3 報價並輸出 router.call(calldata) payload",
    }


def _calldata_from_orders(*orders: dict[str, Any]) -> str | None:
    """
    從訂單資料中取出鏈上 calldata。

    輸入：
    - `orders`：買單或賣單 payload。

    輸出：
    - 若任一訂單的 `intentJson` 或頂層欄位有 `Calldata` / `calldata`，回傳該字串。
    - 找不到時回傳 `None`，讓前端或區塊鏈銜接層後續補齊。
    """
    for order in orders:
        raw_intent = order.get("intentJson") if isinstance(order.get("intentJson"), dict) else {}
        for source in (order, raw_intent):
            calldata = source.get("Calldata") or source.get("calldata")
            if calldata:
                return str(calldata)
    return None


def _intent_from_order(order: dict[str, Any], fallback_amount_in: Any, fallback_min_amount_out: Any) -> dict[str, Any]:
    """
    從訂單讀取前端已簽署 intent，缺少時補上主腦可推導的數量欄位。

    輸入：
    - `order`：買單或賣單 payload。
    - `fallback_amount_in`：沒有 intent 時使用的 amountIn。
    - `fallback_min_amount_out`：沒有 intent 時使用的 minAmountOut。

    輸出：
    - 回傳文件 payload 使用的 intent dict。
    """
    raw_intent = order.get("intentJson") if isinstance(order.get("intentJson"), dict) else {}
    return {
        "user": raw_intent.get("user"),
        "tokenIn": raw_intent.get("tokenIn"),
        "tokenOut": raw_intent.get("tokenOut"),
        "amountIn": str(raw_intent.get("amountIn")) if raw_intent.get("amountIn") is not None else _number_to_string(fallback_amount_in),
        "minAmountOut": str(raw_intent.get("minAmountOut")) if raw_intent.get("minAmountOut") is not None else _number_to_string(fallback_min_amount_out),
        "deadline": raw_intent.get("deadline"),
        "salt": raw_intent.get("salt"),
        "allowPartialFill": raw_intent.get("allowPartialFill", True),
    }


def _missing_payload_fields(
    intent_a: dict[str, Any],
    signature_a: str | None,
    intent_b: dict[str, Any],
    signature_b: str | None,
) -> list[str]:
    """
    檢查文件 payload 裡還缺哪些前端/錢包欄位。

    輸入：
    - `intent_a`：賣方 intent。
    - `signature_a`：賣方簽名。
    - `intent_b`：買方 intent。
    - `signature_b`：買方簽名。

    輸出：
    - 回傳缺少欄位路徑 list。
    """
    missing: list[str] = []
    for field in REQUIRED_INTENT_FIELDS:
        if intent_a.get(field) in (None, ""):
            missing.append(f"intentA.intent.{field}")
        if intent_b.get(field) in (None, ""):
            missing.append(f"routeDetails.matchedIntentB.intent.{field}")
    if not signature_a:
        missing.append("intentA.signature")
    if not signature_b:
        missing.append("routeDetails.matchedIntentB.signature")
    return missing


def _build_external_request(
    task: dict[str, Any],
    sell_order: dict[str, Any],
    decision_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    建立外部合約資料請求。

    輸入：
    - `task`：runner 任務。
    - `sell_order`：目前主賣單。

    輸出：
    - 回傳 `decisionStatus = request_external_contract_data`。
    """
    return {
        "taskId": task["taskId"],
        "decisionStatus": "request_external_contract_data",
        "sellOrderId": sell_order["id"],
        "reason": "主腦：本地沒有可用 OTC 候選，需要查外部合約狀態",
        "externalQuery": {
            "sourceOrderType": "sell",
            "asset": sell_order["asset"],
            "amount": sell_order["remainingAmount"],
            "minUnitPriceUsdc": sell_order["minUnitPriceUsdc"],
            "syncTargets": _build_uniswap_sync_targets_from_sell_order(sell_order),
        },
        "decisionChain": decision_chain,
    }


def _build_uniswap_sync_targets_from_sell_order(sell_order: dict[str, Any]) -> list[dict[str, Any]]:
    """
    從賣單已簽署 intent 建立 Uniswap V3 quote target。

    輸入：
    - `sell_order`：目前主賣單，可能包含前端傳入的 `intentJson`。

    輸出：
    - intent 具備 `user`、`tokenIn`、`tokenOut`、`amountIn` 時，回傳一筆 Uniswap target。
    - 欄位不足時回傳空 list，表示 runner 需要其他來源補外部查詢參數。
    """
    raw_intent = sell_order.get("intentJson") if isinstance(sell_order.get("intentJson"), dict) else {}
    user = raw_intent.get("user")
    token_in = raw_intent.get("tokenIn")
    token_out = raw_intent.get("tokenOut")
    amount_in = raw_intent.get("amountIn")
    if not all([user, token_in, token_out, amount_in]):
        return []

    target = {
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


def _valid_external_candidates(external_context: dict[str, Any]) -> list[dict[str, Any]]:
    """
    從外部合約上下文挑出有效候選。

    輸入：
    - `external_context`：`blockchain_sync.get_external_contract_context()` 回傳資料。

    輸出：
    - 回傳有效候選 list。
    """
    valid: list[dict[str, Any]] = []
    for item in external_context.get("candidates", []):
        candidate = item.get("candidate", item)
        if candidate.get("isValid") is True:
            valid.append(candidate)
    return valid


def _select_external_candidate_with_calldata(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    選出可直接交給 router.call(calldata) 的外部候選。

    輸入：
    - `candidates`：已通過 `isValid` 的外部候選。

    輸出：
    - 找到含 `reads.Calldata` 的候選時回傳該候選。
    - 找不到時回傳 `None`。
    """
    for candidate in candidates:
        reads = candidate.get("reads") or {}
        calldata = reads.get("Calldata")
        if isinstance(calldata, str) and calldata.startswith("0x"):
            return candidate
    return None


def _build_rejected_decision(
    task: dict[str, Any],
    sell_order: dict[str, Any],
    reason: str,
    decision_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    建立 rejected 決策。

    輸入：
    - `task`：runner 任務。
    - `sell_order`：目前主賣單。
    - `reason`：拒絕原因。

    輸出：
    - 回傳 `decisionStatus = rejected`。
    """
    return {
        "taskId": task["taskId"],
        "decisionStatus": "rejected",
        "sellOrderId": sell_order["id"],
        "failureReason": reason,
        "decisionChain": decision_chain,
    }


def _number_to_string(value: Any) -> str:
    """
    將數字轉成穩定字串，避免 JSON 裡出現不必要的小數尾巴。

    輸入：
    - `value`：數字或可轉成 float 的值。

    輸出：
    - 回傳字串。
    """
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)
