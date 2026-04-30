from scripts import main_brain


def make_task(candidate_buy_orders=None, sell_intent=None, sell_signature=None):
    """建立主腦測試用 task。"""
    return {
        "taskId": "task-1",
        "sellOrder": {
            "id": 10,
            "accountName": "seller",
            "accountLevelSnapshot": "free",
            "asset": "WETH",
            "amount": 1,
            "remainingAmount": 1,
            "minUnitPriceUsdc": 2900,
            "maxSplits": 3,
            "maxFeePercent": 0.3,
            "status": "pending",
            "attempts": 0,
            "createdAt": "2026-04-30T00:00:00+00:00",
            "updatedAt": "2026-04-30T00:00:00+00:00",
            "queueAt": "2026-04-30T00:00:00+00:00",
            "operationNote": "",
            "intentJson": sell_intent,
            "signature": sell_signature,
            "hasIntent": sell_intent is not None,
            "hasSignature": sell_signature is not None,
        },
        "candidateBuyOrders": candidate_buy_orders or [],
        "matchingRule": {
            "assetMustMatch": True,
            "buyMaxUnitPriceMustCoverSellMinUnitPrice": True,
        },
    }


def make_buy_order(order_id: int, max_price: float, remaining: float = 2, intent=None, signature=None):
    """建立主腦測試用買單候選。"""
    return {
        "id": order_id,
        "accountName": f"buyer-{order_id}",
        "accountLevelSnapshot": "free",
        "asset": "WETH",
        "amount": remaining,
        "remainingAmount": remaining,
        "maxUnitPriceUsdc": max_price,
        "maxSplits": 3,
        "maxFeePercent": 0.3,
        "status": "pending",
        "attempts": 0,
        "createdAt": "2026-04-30T00:00:00+00:00",
        "updatedAt": "2026-04-30T00:00:00+00:00",
        "operationNote": "",
        "intentJson": intent,
        "signature": signature,
        "hasIntent": intent is not None,
        "hasSignature": signature is not None,
    }


def test_decide_outputs_document_payload_for_internal_otc_candidate() -> None:
    """測試主腦在內部可撮合時輸出嚴格區塊鏈 payload。"""
    task = make_task([make_buy_order(1, 3000), make_buy_order(2, 3100)])

    decision = main_brain.decide(task)
    payload = decision["executionPayload"]

    assert decision["decisionStatus"] == "proposed_execution"
    assert decision["sellOrderId"] == 10
    assert decision["matches"] == [{"buyOrderId": 2, "filledAmount": 1.0, "unitPriceUsdc": 2900.0}]
    assert [step["agentName"] for step in decision["decisionChain"]] == [
        "data_collector_agent",
        "internal_otc_agent",
        "strategy_scoring_agent",
        "payload_builder_agent",
    ]
    assert decision["decisionChain"][1]["details"]["selectedBuyOrderId"] == 2
    assert decision["decisionChain"][2]["details"]["strategyScores"]["strategyB_OTC"] == 100
    assert set(payload) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert payload["actionType"] == 1
    assert payload["executeAmountIn"] == "1"
    assert payload["intentA"]["intent"]["amountIn"] == "1"
    assert payload["intentA"]["intent"]["minAmountOut"] == "2900"
    assert payload["intentA"]["intent"]["user"] is None
    assert payload["intentA"]["signature"] is None
    assert payload["routeDetails"]["Calldata"] is None
    assert payload["routeDetails"]["matchedIntentB"]["executeAmountInB"] == "2900"
    assert payload["routeDetails"]["matchedIntentB"]["signature"] is None
    assert payload["routeDetails"]["treasuryAmountOut"] is None


def test_decide_uses_frontend_intent_and_signature_when_available() -> None:
    """測試主腦會使用前端已補齊的 intent/signature 並輸出嚴格 payload。"""
    sell_intent = {
        "user": "0xSeller",
        "tokenIn": "0xWETH",
        "tokenOut": "0xUSDC",
        "amountIn": "1",
        "minAmountOut": "2900",
        "deadline": 1999999999,
        "salt": "0xsell",
        "allowPartialFill": True,
    }
    buy_intent = {
        "user": "0xBuyer",
        "tokenIn": "0xUSDC",
        "tokenOut": "0xWETH",
        "amountIn": "3000",
        "minAmountOut": "1",
        "deadline": 1999999999,
        "salt": "0xbuy",
        "allowPartialFill": True,
    }
    task = make_task(
        [make_buy_order(1, 3000, intent=buy_intent, signature="0xbuy_signature")],
        sell_intent=sell_intent,
        sell_signature="0xsell_signature",
    )

    decision = main_brain.decide(task)
    payload = decision["executionPayload"]

    assert set(payload) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert decision["decisionChain"][-1]["agentName"] == "payload_builder_agent"
    assert payload["intentA"]["intent"] == sell_intent
    assert payload["intentA"]["signature"] == "0xsell_signature"
    assert payload["routeDetails"]["Calldata"] is None
    assert payload["routeDetails"]["matchedIntentB"]["intent"] == buy_intent
    assert payload["routeDetails"]["matchedIntentB"]["signature"] == "0xbuy_signature"


def test_decide_carries_calldata_from_frontend_intent_when_available() -> None:
    """測試主腦會把前端提供的 Calldata 放入 routeDetails。"""
    sell_intent = {
        "user": "0xSeller",
        "tokenIn": "0xWETH",
        "tokenOut": "0xUSDC",
        "amountIn": "1",
        "minAmountOut": "2900",
        "deadline": 1999999999,
        "salt": "0xsell",
        "allowPartialFill": True,
        "Calldata": "0x04e45aaf",
    }
    buy_intent = {
        "user": "0xBuyer",
        "tokenIn": "0xUSDC",
        "tokenOut": "0xWETH",
        "amountIn": "3000",
        "minAmountOut": "1",
        "deadline": 1999999999,
        "salt": "0xbuy",
        "allowPartialFill": True,
    }
    decision = main_brain.decide(
        make_task(
            [make_buy_order(1, 3000, intent=buy_intent, signature="0xbuy_signature")],
            sell_intent=sell_intent,
            sell_signature="0xsell_signature",
        )
    )

    payload = decision["executionPayload"]

    assert payload["routeDetails"]["Calldata"] == "0x04e45aaf"
    assert "Calldata" not in payload["intentA"]["intent"]


def test_decide_requests_external_data_when_no_internal_candidate() -> None:
    """測試沒有本地候選且尚未查外部時，主腦要求外部合約資料。"""
    decision = main_brain.decide(make_task())

    assert decision["decisionStatus"] == "request_external_contract_data"
    assert decision["sellOrderId"] == 10
    assert [step["agentName"] for step in decision["decisionChain"]] == [
        "data_collector_agent",
        "internal_otc_agent",
        "strategy_scoring_agent",
        "external_contract_request_agent",
    ]
    assert decision["decisionChain"][2]["status"] == "need_external_contract_data"
    assert decision["externalQuery"]["sourceOrderType"] == "sell"
    assert decision["externalQuery"]["asset"] == "WETH"
    assert decision["externalQuery"]["syncTargets"] == []


def test_decide_builds_uniswap_sync_target_from_sell_intent() -> None:
    """測試賣單 intent 欄位足夠時，外部查詢會帶 Uniswap V3 target。"""
    sell_intent = {
        "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "tokenIn": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        "tokenOut": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
        "amountIn": "100000000",
        "minAmountOut": "1",
        "deadline": 1999999999,
        "salt": "0x" + "11" * 32,
        "allowPartialFill": True,
        "chainId": 11155111,
        "fee": 100,
        "priceLimit": 0,
    }

    decision = main_brain.decide(make_task(sell_intent=sell_intent, sell_signature="0xsignature"))
    target = decision["externalQuery"]["syncTargets"][0]

    assert target["tokenIn"] == sell_intent["tokenIn"]
    assert target["tokenOut"] == sell_intent["tokenOut"]
    assert target["amount"] == "100000000"
    assert target["swapper"] == sell_intent["user"]
    assert target["recipient"] == sell_intent["user"]
    assert target["chainId"] == 11155111
    assert target["fee"] == 100
    assert target["priceLimit"] == 0


def test_decide_outputs_external_dex_payload_when_calldata_is_available() -> None:
    """測試主腦拿到外部 Calldata 後，輸出 actionType=0 的 DEX payload。"""
    external_context = {
        "candidates": [
            {
                "candidate": {
                    "targetId": "external-1",
                    "isValid": True,
                    "reads": {
                        "amountIn": "100000000",
                        "amountOut": "11834460714425122",
                        "Calldata": "0x04e45aaf",
                    },
                }
            }
        ]
    }

    decision = main_brain.decide(make_task(), external_context)
    payload = decision["executionPayload"]

    assert decision["decisionStatus"] == "proposed_execution"
    assert decision["sellOrderId"] == 10
    assert decision["matches"] == []
    assert [step["agentName"] for step in decision["decisionChain"]] == [
        "data_collector_agent",
        "internal_otc_agent",
        "strategy_scoring_agent",
        "external_context_agent",
        "payload_builder_agent",
    ]
    assert decision["decisionChain"][3]["details"]["validCandidateCount"] == 1
    assert decision["decisionChain"][3]["details"]["selectedTargetId"] == "external-1"
    assert set(payload) == {"intentA", "actionType", "executeAmountIn", "routeDetails"}
    assert payload["actionType"] == 0
    assert payload["executeAmountIn"] == "100000000"
    assert payload["routeDetails"] == {
        "Calldata": "0x04e45aaf",
        "matchedIntentB": None,
        "treasuryAmountOut": None,
    }


def test_decide_rejects_external_context_when_calldata_is_missing() -> None:
    """測試外部候選缺 Calldata 時，主腦保守拒絕。"""
    external_context = {
        "candidates": [
            {
                "candidate": {
                    "targetId": "external-1",
                    "isValid": True,
                    "reads": {"amountIn": "100000000"},
                }
            }
        ]
    }

    decision = main_brain.decide(make_task(), external_context)

    assert decision["decisionStatus"] == "rejected"
    assert "沒有可送 router.call(calldata) 的 Calldata" in decision["failureReason"]
