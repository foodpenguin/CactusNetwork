# Output Format Memory

## 主腦輸出總規則

主腦輸出必須是單一 JSON object，不可包含 Markdown、說明文字或額外 envelope。

## 內部 OTC 提案

內部 OTC 使用 `actionType = 1`，需要有非空 `matches`。

必要語意：

- `sellOrderId` 必須等於本輪 task 的 `sellOrder.id`。
- 每筆 match 必須包含 `buyOrderId`、`filledAmount`、`unitPriceUsdc`。
- `filledAmount` 不可超過賣單或買單剩餘量。
- `executionPayload.routeDetails.matchedIntentB` 可由後端用買單資料補齊。

## 外部 DEX 提案

外部 DEX 使用 `actionType = 0`，`matches` 必須是空陣列。

嚴格 payload 形狀：

```json
{
  "intentA": {
    "intent": null,
    "signature": null
  },
  "actionType": 0,
  "executeAmountIn": "100000000",
  "routeDetails": {
    "Calldata": "0x...",
    "matchedIntentB": null,
    "treasuryAmountOut": null
  }
}
```

## 外部資料請求

當本地沒有候選且 `externalContext` 尚未存在時，輸出：

```json
{
  "decisionStatus": "request_external_contract_data",
  "sellOrderId": 1,
  "reason": "本地沒有候選買單，需要查詢外部 Uniswap V3 報價",
  "externalQuery": {
    "sourceOrderType": "sell",
    "asset": "WETH",
    "amount": 100000000,
    "minUnitPriceUsdc": 0,
    "syncTargets": []
  }
}
```

## 拒絕

當資料不足、格式不完整、沒有可用 calldata，或風險無法判斷時，輸出 `rejected` 並提供 `failureReason`。
