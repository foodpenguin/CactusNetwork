# CactusNetwork API 速查

更新日期：2026-05-02

這份文件只寫「怎麼呼叫、傳進什麼、回來什麼」。

## 區塊鏈端唯一格式

後端傳給區塊鏈端的交易資料，只能使用 execution response 裡的 `payload` 欄位。

`payload` 必須完全長這樣，不能再包其他外層欄位：

```json
{
  "intentA": {
    "intent": {
      "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
      "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
      "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
      "amountIn": "1000000000000000000",
      "minAmountOut": "3000000000",
      "deadline": 1735689600,
      "salt": "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
      "allowPartialFill": true
    },
    "signature": "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b200"
  },
  "actionType": 0,
  "executeAmountIn": "500000000000000000",
  "routeDetails": {
    "Calldata": "0x04e45aaf000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb480000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb9226600000000000000000000000000000000000000000000000006f05b59d3b2000000000000000000000000000000000000000000000000000000000000b2d05e000000000000000000000000000000000000000000000000000000000000000000",
    "matchedIntentB": null,
    "treasuryAmountOut": null
  }
}
```

`executionId`、`status`、`readyForExecutor`、`missingFields` 是後端追蹤欄位，不是鏈上 payload。

## Base URL

公開 API：

```text
http://127.0.0.1:8000
```

內部 API：

```text
http://127.0.0.1:8001
```

內部 API 都要帶：

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

## 前端會用到的 API

目前前端會用到：

```text
POST /accounts
POST /login
POST /buy-orders
POST /sell-orders
GET /buy-orders
GET /sell-orders
GET /executions
```

## 1. 建立帳號

```text
POST /accounts
```

### 傳入

```json
{
  "account_name": "alice",
  "password": "password123",
  "public_key": "0xAlicePublicKey"
}
```

### 傳出

```json
{
  "message": "帳號已建立",
  "accountName": "alice",
  "publicKey": "0xAlicePublicKey",
  "accountLevel": "free",
  "day": 0,
  "createdAt": "2026-05-01T00:00:00+00:00"
}
```

### 注意

前端不能傳：

```text
account_level
day
```

這兩個欄位只能由後台改。

## 2. 登入帳號

```text
POST /login
```

### 傳入

```json
{
  "account_name": "alice",
  "password": "password123"
}
```

### 傳出

```json
{
  "message": "登入成功",
  "tokenType": "Bearer",
  "accessToken": "token_string",
  "expiresAt": "2026-05-01T12:00:00+00:00"
}
```

### 後續怎麼用

建立買單、賣單時，在 header 放：

```text
Authorization: Bearer <accessToken>
```

## 3. 建立買單

```text
POST /buy-orders
```

### Header

```text
Authorization: Bearer <accessToken>
```

### 傳入

```json
{
  "asset": "WETH",
  "amount": 1,
  "max_unit_price_usdc": 3000,
  "max_splits": 3,
  "max_fee_percent": 0.3,
  "intent_json": {
    "user": "0x1111111111111111111111111111111111111111",
    "tokenIn": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "tokenOut": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
    "amountIn": "3000000000",
    "minAmountOut": "1000000000000000000",
    "deadline": 1999999999,
    "salt": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "allowPartialFill": true
  },
  "signature": "0xcdcdcdcdcdcdcdcd"
}
```

### 意思

```text
我要買 1 WETH
每 1 WETH 最多出 3000 USDC
最多接受拆成 3 單
手續費希望 0.3% 以內
```

### 傳出

```json
{
  "message": "買單已建立",
  "buyOrderId": 1,
  "accountName": "alice",
  "accountLevelSnapshot": "free",
  "asset": "WETH",
  "amount": 1,
  "remainingAmount": 1,
  "maxUnitPriceUsdc": 3000,
  "maxSplits": 3,
  "maxFeePercent": 0.3,
  "status": "pending",
  "attempts": 0,
  "hasIntent": true,
  "hasSignature": true,
  "createdAt": "2026-05-01T00:00:00+00:00"
}
```

## 4. 建立賣單

```text
POST /sell-orders
```

### Header

```text
Authorization: Bearer <accessToken>
```

### 傳入

```json
{
  "asset": "WETH",
  "amount": 1,
  "min_unit_price_usdc": 2900,
  "max_splits": 3,
  "max_fee_percent": 0.3,
  "intent_json": {
    "user": "0x1111111111111111111111111111111111111111",
    "tokenIn": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
    "tokenOut": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "amountIn": "1000000000000000000",
    "minAmountOut": "2900000000",
    "deadline": 1999999999,
    "salt": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "allowPartialFill": true
  },
  "signature": "0xcdcdcdcdcdcdcdcd"
}
```

### 意思

```text
我要賣 1 WETH
每 1 WETH 最低接受 2900 USDC
最多接受拆成 3 單
手續費希望 0.3% 以內
```

### 傳出

```json
{
  "message": "賣單已建立",
  "sellOrderId": 1,
  "accountName": "alice",
  "accountLevelSnapshot": "free",
  "asset": "WETH",
  "amount": 1,
  "remainingAmount": 1,
  "minUnitPriceUsdc": 2900,
  "maxSplits": 3,
  "maxFeePercent": 0.3,
  "status": "pending",
  "attempts": 0,
  "hasIntent": true,
  "hasSignature": true,
  "createdAt": "2026-05-01T00:00:00+00:00",
  "queueAt": "2026-05-01T00:00:00+00:00"
}
```

## 5. 查詢自己的買單

```text
GET /buy-orders
```

### Header

```text
Authorization: Bearer <accessToken>
```

### 傳出

```json
[
  {
    "direction": "BUY",
    "orderId": 1,
    "buyOrderId": 1,
    "accountName": "alice",
    "asset": "WETH",
    "amount": 1,
    "remainingAmount": 1,
    "maxUnitPriceUsdc": 3000,
    "status": "pending",
    "attempts": 0,
    "operationNote": "",
    "hasIntent": true,
    "hasSignature": true,
    "createdAt": "2026-05-01T00:00:00+00:00",
    "updatedAt": "2026-05-01T00:00:00+00:00"
  }
]
```

## 6. 查詢自己的賣單

```text
GET /sell-orders
```

### Header

```text
Authorization: Bearer <accessToken>
```

### 傳出

```json
[
  {
    "direction": "SELL",
    "orderId": 1,
    "sellOrderId": 1,
    "accountName": "alice",
    "asset": "WETH",
    "amount": 1,
    "remainingAmount": 1,
    "minUnitPriceUsdc": 2900,
    "status": "pending",
    "attempts": 0,
    "operationNote": "",
    "hasIntent": true,
    "hasSignature": true,
    "createdAt": "2026-05-01T00:00:00+00:00",
    "updatedAt": "2026-05-01T00:00:00+00:00",
    "queueAt": "2026-05-01T00:00:00+00:00"
  }
]
```

## 7. 查詢自己的 execution 狀態

```text
GET /executions
```

### Header

```text
Authorization: Bearer <accessToken>
```

### 傳出

```json
[
  {
    "executionId": "execution:1:match:1",
    "sellOrderId": 1,
    "status": "failed",
    "failureReason": "KeeperHub workflow succeeded without chain tx hash",
    "relatedBy": "sell_order",
    "createdAt": "2026-05-01T00:00:00+00:00",
    "updatedAt": "2026-05-01T00:01:00+00:00",
    "confirmedAt": null
  }
]
```

`relatedBy` 可能是：

```text
sell_order
buy_order
```

## 錢包簽名資料

買單、賣單都必須帶：

```json
{
  "intent_json": {
    "user": "0x1111111111111111111111111111111111111111",
    "tokenIn": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
    "tokenOut": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "amountIn": "1000000000000000000",
    "minAmountOut": "2900000000",
    "deadline": 1999999999,
    "salt": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "allowPartialFill": true
  },
  "signature": "0xcdcdcdcdcdcdcdcd"
}
```

不帶或格式不完整時，後端會直接拒絕建立訂單，不會讓壞單進入中控佇列。

## 後端 / 區塊鏈端會用到的 API

這些不是一般前端要碰的，是給後端中控與區塊鏈端串接用。

## 8. 觸發後端媒合

```text
POST /internal/matching/run
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

```json
{
  "agent": "grok",
  "candidate_limit": 5,
  "drain_until_empty": true,
  "max_cycles": 100
}
```

`agent` 可用：

```text
grok
main-brain
simulated
```

預設值：

```text
agent = grok
candidate_limit = 5
drain_until_empty = true
max_cycles = 100
```

如果只想跑一筆賣單，傳：

```json
{
  "drain_until_empty": false
}
```

### 傳出

```json
{
  "status": "matching_drain_completed",
  "agent": "grok",
  "stopReason": "no_processable_sell_order",
  "cyclesRun": 2,
  "cycles": [
    {
      "status": "matching_cycle_completed",
      "runnerResult": {
        "status": "execution_proposed",
        "applyResult": {
          "executionId": "exec_20260501_000001",
          "executionStatus": "proposed"
        }
      }
    }
  ]
}
```

### 意思

這支 API 預設會讓後端持續處理賣單，直到沒有可派發的 pending 賣單：

```text
刷新 timeout
讀取下一筆可派發賣單
交給 Grok 主腦判斷
如果可成交，產生嚴格區塊鏈 payload
繼續處理下一筆賣單
直到沒有可派發賣單
```

系統不會在這一步直接上鏈。

若某筆賣單已經產生 `proposed` 或 `dispatched` execution，系統會等區塊鏈端回覆 confirmed / failed，不會重複派發同一筆賣單。

## 9. 取得待送出的交易請求

```text
GET /internal/executions/pending
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### Query

```text
limit=20
ready_only=false
```

### 傳出

```json
[
  {
    "executionId": "exec_20260501_000001",
    "status": "proposed",
    "readyForExecutor": true,
    "missingFields": [],
    "payload": {
      "intentA": {
        "intent": {
          "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
          "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
          "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
          "amountIn": "1000000000000000000",
          "minAmountOut": "3000000000",
          "deadline": 1735689600,
          "salt": "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
          "allowPartialFill": true
        },
        "signature": "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b200"
      },
      "actionType": 0,
      "executeAmountIn": "500000000000000000",
      "routeDetails": {
        "Calldata": "0x04e45aaf000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb480000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb9226600000000000000000000000000000000000000000000000006f05b59d3b2000000000000000000000000000000000000000000000000000000000000b2d05e000000000000000000000000000000000000000000000000000000000000000000",
        "matchedIntentB": null,
        "treasuryAmountOut": null
      }
    },
    "createdAt": "2026-05-01T00:00:00+00:00"
  }
]
```

### 意思

區塊鏈端從這裡拿交易請求。

真正傳給區塊鏈的內容只有 `payload` 這個欄位，格式固定為：

```json
{
  "intentA": {
    "intent": {},
    "signature": "0x..."
  },
  "actionType": 0,
  "executeAmountIn": "500000000000000000",
  "routeDetails": {
    "Calldata": "0x04e45aaf000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb480000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb9226600000000000000000000000000000000000000000000000006f05b59d3b2000000000000000000000000000000000000000000000000000000000000b2d05e000000000000000000000000000000000000000000000000000000000000000000",
    "matchedIntentB": null,
    "treasuryAmountOut": null
  }
}
```

不要把 `executionId`、`status`、`readyForExecutor`、`missingFields` 傳進鏈上合約；那些只是後端追蹤用。

## 10. 取得單筆交易請求

```text
GET /internal/executions/{execution_id}
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳出

```json
{
  "executionId": "exec_20260501_000001",
  "status": "proposed",
  "readyForExecutor": true,
  "missingFields": [],
  "payload": {
    "intentA": {
      "intent": {
        "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "amountIn": "1000000000000000000",
        "minAmountOut": "3000000000",
        "deadline": 1735689600,
        "salt": "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
        "allowPartialFill": true
      },
      "signature": "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b200"
    },
    "actionType": 0,
    "executeAmountIn": "500000000000000000",
    "routeDetails": {
      "Calldata": "0x04e45aaf000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb480000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb9226600000000000000000000000000000000000000000000000006f05b59d3b2000000000000000000000000000000000000000000000000000000000000b2d05e000000000000000000000000000000000000000000000000000000000000000000",
      "matchedIntentB": null,
      "treasuryAmountOut": null
    }
  }
}
```

## 11. 標記交易已送出

```text
POST /internal/executions/{execution_id}/dispatch
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

```json
{
  "dispatch_metadata": {
    "sentBy": "blockchain-service",
    "txHash": "0xPendingTxHash"
  }
}
```

### 傳出

```json
{
  "executionId": "exec_20260501_000001",
  "executionStatus": "dispatched",
  "dispatchMetadata": {
    "sentBy": "blockchain-service",
    "txHash": "0xPendingTxHash"
  }
}
```

### 意思

區塊鏈端拿到交易請求並送出後，用這支 API 告訴後端：

```text
這筆已經送出，正在等鏈上結果。
```

## 12. 直接送到 KeeperHub webhook

```text
POST /internal/executions/{execution_id}/keeperhub/dispatch
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

通常不用傳內容，後端會使用預設 KeeperHub webhook：

```json
{}
```

也可以指定：

```json
{
  "webhook_url": "https://app.keeperhub.com/api/workflows/o2o3h3yf8s6ps4ogg8h81/webhook",
  "timeout_seconds": 60,
  "webhook_headers": {
    "Authorization": "Bearer <KeeperHub token>"
  },
  "wait_for_final_result": true,
  "poll_interval_seconds": 5,
  "max_wait_seconds": 300,
  "status_api_base": "https://app.keeperhub.com/api/workflows/executions",
  "status_headers": {
    "Authorization": "Bearer <KeeperHub API token>"
  }
}
```

如果 KeeperHub webhook 需要授權，也可以在 `.env` 放：

```text
KEEPERHUB_WEBHOOK_AUTHORIZATION=Bearer <KeeperHub token>
```

或：

```text
KEEPERHUB_WEBHOOK_TOKEN=<KeeperHub token>
```

### 送出前鏈上預檢

後端送 KeeperHub 前可以先用 `eth_call` 檢查小金庫與 intent 剩餘額度：

```text
ONCHAIN_PREFLIGHT_CHECKS=auto
SP_TESTNET_RPC_URL=<Sepolia RPC URL>
INTENT_VAULT_ADDRESS=<IntentVault address>
SETTLEMENT_ROUTER_ADDRESS=<SettlementRouter address>
PROTOCOL_TREASURY_ADDRESS=<ProtocolTreasury address，actionType=2 才需要>
```

模式：

```text
auto      設定齊全才檢查，缺設定時跳過
required  設定缺失、RPC 錯誤、餘額不足都不送 KeeperHub
disabled  關閉檢查
```

預檢會讀：

```text
IntentVault.balances(intent.user, intent.tokenIn)
SettlementRouter.filledAmountIn(intentHash)
ProtocolTreasury tokenOut balance，僅 actionType=2
```

如果 vault 餘額不足，或 intent 已經被部分成交到剩餘額度不足，後端會拒絕送出並把自動收尾流程中的 execution 標成 failed，訂單本身不會被扣量。

### 後端實際送給 KeeperHub 的內容

後端只會把 execution 裡的嚴格 `payload` 送出去：

```json
{
  "intentA": {
    "intent": {
      "user": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
      "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
      "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
      "amountIn": "1000000000000000000",
      "minAmountOut": "3000000000",
      "deadline": 1735689600,
      "salt": "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b",
      "allowPartialFill": true
    },
    "signature": "0xa1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b200"
  },
  "actionType": 0,
  "executeAmountIn": "500000000000000000",
  "routeDetails": {
    "Calldata": "0x04e45aaf...",
    "matchedIntentB": null,
    "treasuryAmountOut": null
  }
}
```

不會把 `executionId`、`status`、`readyForExecutor` 一起送給 KeeperHub。

### 傳出

如果 KeeperHub 只回覆已接收：

```json
{
  "status": "keeperhub_dispatch_completed",
  "executionId": "exec_20260501_000001",
  "executionStatus": "dispatched",
  "keeperhub": {
    "httpStatusCode": 202,
    "body": {
      "id": "keeperhub_execution_id",
      "status": "running"
    }
  }
}
```

這代表 KeeperHub 已經開始處理，但還不是最終結果。後端會把本地 execution 留在：

```text
dispatched
```

直到後續拿到 KeeperHub `success / error / failed / cancelled`，才會真正結束。

如果 dispatch 時傳：

```json
{
  "wait_for_final_result": true
}
```

後端會在這次請求內持續查 KeeperHub status API，直到拿到 success / failed 或超過 `max_wait_seconds`。

如果 KeeperHub 直接回覆：

```json
{
  "status": "confirmed",
  "tx_hash": "0xConfirmedTxHash",
  "block_number": 123456
}
```

後端會自動把該筆 execution 當成 confirmed 處理，並更新訂單。

## 13. 自動刷新 KeeperHub 結果

```text
POST /internal/executions/keeperhub/refresh
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

```json
{
  "limit": 20,
  "timeout_seconds": 60
}
```

也可以指定 KeeperHub status API：

```json
{
  "limit": 20,
  "timeout_seconds": 60,
  "status_api_base": "https://app.keeperhub.com/api/workflows/executions",
  "status_headers": {
    "Authorization": "Bearer <KeeperHub API token>"
  }
}
```

`.env` 可設定：

```text
KEEPERHUB_STATUS_AUTHORIZATION=Bearer <KeeperHub API token>
```

或：

```text
KEEPERHUB_STATUS_TOKEN=<KeeperHub API token>
```

也支援通用名稱：

```text
KEEPERHUB_API_AUTHORIZATION=Bearer <KeeperHub API token>
KEEPERHUB_API_TOKEN=<KeeperHub API token>
```

### 傳出

```json
{
  "status": "keeperhub_refresh_completed",
  "checkedCount": 2,
  "waiting": [
    {
      "executionId": "execution:1:match:1",
      "keeperhubExecutionId": "keeperhub_execution_id",
      "keeperhubStatus": "running"
    }
  ],
  "finalized": [
    {
      "executionId": "execution:1:match:2",
      "keeperhubExecutionId": "keeperhub_execution_id_2",
      "executionStatus": "confirmed",
      "keeperhubStatus": "success"
    }
  ],
  "skipped": [],
  "errors": []
}
```

### 意思

後端只會刷新本地狀態為：

```text
dispatched
```

的 execution。

KeeperHub 回覆：

```text
running / pending
```

時，後端不會扣訂單，也不會結束 execution。

KeeperHub 回覆：

```text
success / confirmed / completed
```

時，後端會將 execution 視為 confirmed，正式更新買賣單剩餘數量。

KeeperHub 回覆：

```text
error / failed / cancelled
```

時，後端會將 execution 視為 failed，不更新買賣單。

如果同一筆 execution 已經是 confirmed 或 failed，後端不會再處理一次，所以重複刷新不會重複扣單。

注意：webhook trigger token 與 KeeperHub status API token 可能不是同一種 token。若 status API 回 401，請向 KeeperHub 取得可讀 execution status 的 API token，或改由 KeeperHub workflow 在結束時呼叫下一節的 result callback。

## 14. 自動收尾 executions

```text
POST /internal/executions/reconcile
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

```json
{
  "limit": 20,
  "dispatch_ready": true,
  "expire_invalid": true,
  "refresh_dispatched": true,
  "wait_for_final_result": true,
  "timeout_seconds": 60,
  "poll_interval_seconds": 5,
  "max_wait_seconds": 300
}
```

### 傳出

```json
{
  "status": "execution_reconcile_completed",
  "expired": [],
  "dispatched": [],
  "skipped": [],
  "errors": [],
  "summary": {
    "expiredCount": 0,
    "dispatchedCount": 0,
    "skippedCount": 0,
    "errorCount": 0
  }
}
```

### 意思

這支 API 會做四件事：

```text
1. 檢查 dispatched execution 是否已有 KeeperHub 最終結果
2. 把缺欄位、deadline 過期、或本地訂單已不可處理的 proposed execution 標成 failed
3. 對 ready 的 proposed execution 做鏈上預檢
4. 把預檢通過的 execution 自動送到 KeeperHub，並等待 confirmed / failed
```

正式 VM 也有常駐服務：

```text
cactus-matching-loop
cactus-execution-reconciler
```

所以一般情況不需要手動呼叫這支 API。

## 15. 回報鏈上結果

```text
POST /internal/executions/{execution_id}/result
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 成功時傳入

```json
{
  "status": "confirmed",
  "tx_hash": "0xConfirmedTxHash",
  "block_number": 123456,
  "raw_receipt": {}
}
```

### 失敗時傳入

```json
{
  "status": "failed",
  "failure_reason": "reverted"
}
```

### 傳出

```json
{
  "executionId": "exec_20260501_000001",
  "executionStatus": "confirmed",
  "resultStatus": "confirmed"
}
```

### 意思

只有收到這支 API 的 `confirmed` 回覆後，後端才會正式把訂單視為成交。

## 16. 讀鏈上狀態確認結果

```text
POST /internal/executions/{execution_id}/onchain/confirm
```

### Header

```text
X-Internal-Token: <INTERNAL_API_TOKEN>
```

### 傳入

```json
{
  "tx_hash": "0xOptionalTxHash",
  "raw_keeperhub_result": {
    "status": "success"
  }
}
```

`tx_hash` 可以不傳。這支 API 會自己讀 `SettlementRouter.filledAmountIn(intentHash)`。

### confirmed 傳出

```json
{
  "status": "onchain_confirmation_accepted",
  "executionId": "exec_20260501_000001",
  "executionStatus": "confirmed",
  "onchainEvidence": {
    "status": "confirmed",
    "confirmed": true,
    "checks": []
  }
}
```

### 尚未確認傳出

```json
{
  "status": "onchain_confirmation_not_found",
  "executionId": "exec_20260501_000001",
  "executionStatus": "dispatched",
  "failureReason": "intentA filledAmountIn=0 < required=1000000000000000000"
}
```

### 意思

KeeperHub 如果回 `success` 但沒有帶交易 hash，後端會用這個鏈上確認邏輯補判定。

確認方式：

```text
filledAmountIn >= executeAmountIn
```

如果是內部買賣單撮合，`intentA` 和 `matchedIntentB` 都必須達標。

## 最短串接流程

前端：

```text
1. POST /accounts
2. POST /login
3. POST /buy-orders
4. POST /sell-orders
```

後端中控：

```text
5. cactus-matching-loop 自動執行 Grok 媒合
```

區塊鏈端：

```text
6. cactus-execution-reconciler 自動送 KeeperHub
7. cactus-execution-reconciler 自動等 KeeperHub confirmed / failed
```

如果不用常駐服務，也可以手動跑：

```text
5B. POST /internal/matching/run
6B. POST /internal/executions/reconcile
```

如果不用 KeeperHub，也可以維持手動區塊鏈端流程：

```text
6C. GET /internal/executions/pending
7C. POST /internal/executions/{execution_id}/dispatch
8C. POST /internal/executions/{execution_id}/result
```

一句話：

```text
前端只負責建立帳號、登入、送買單、送賣單。
後端只負責媒合並輸出嚴格區塊鏈 payload。
KeeperHub 或區塊鏈端只負責拿 payload、送鏈上、回報結果。
```
