# CactusNetwork API 速查

更新日期：2026-05-01

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

目前前端只需要四支：

```text
POST /accounts
POST /login
POST /buy-orders
POST /sell-orders
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
  "max_fee_percent": 0.3
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
  "hasIntent": false,
  "hasSignature": false,
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
  "max_fee_percent": 0.3
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
  "hasIntent": false,
  "hasSignature": false,
  "createdAt": "2026-05-01T00:00:00+00:00",
  "queueAt": "2026-05-01T00:00:00+00:00"
}
```

## 可選：錢包簽名資料

買單、賣單都可以額外帶：

```json
{
  "intent_json": {
    "user": "0xUser",
    "tokenIn": "0xTokenIn",
    "tokenOut": "0xTokenOut",
    "amountIn": "1",
    "minAmountOut": "2900",
    "deadline": 1999999999,
    "salt": "0xsalt",
    "allowPartialFill": true
  },
  "signature": "0xsignature"
}
```

不帶也可以建立訂單。

如果不帶，後端產生給區塊鏈端的交易請求可能會標記：

```text
incomplete_missing_frontend_wallet_fields
```

意思是：這筆交易資料還缺前端錢包欄位，不能直接上鏈。

## 後端 / 區塊鏈端會用到的 API

這些不是一般前端要碰的，是給後端中控與區塊鏈端串接用。

## 5. 觸發後端媒合

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

## 6. 取得待送出的交易請求

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

## 7. 取得單筆交易請求

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

## 8. 標記交易已送出

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

## 9. 直接送到 KeeperHub webhook

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

## 10. 自動刷新 KeeperHub 結果

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

## 11. 回報鏈上結果

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
5. POST /internal/matching/run
```

區塊鏈端：

```text
6. GET /internal/executions/pending
7A. POST /internal/executions/{execution_id}/keeperhub/dispatch
8A. POST /internal/executions/keeperhub/refresh
```

如果不用後端直接送 KeeperHub，也可以維持手動區塊鏈端流程：

```text
7B. POST /internal/executions/{execution_id}/dispatch
8B. POST /internal/executions/{execution_id}/result
```

一句話：

```text
前端只負責建立帳號、登入、送買單、送賣單。
後端只負責媒合並輸出嚴格區塊鏈 payload。
KeeperHub 或區塊鏈端只負責拿 payload、送鏈上、回報結果。
```
