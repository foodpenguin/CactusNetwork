# CactusNetwork 全通測試指南

更新日期：2026-05-02

這份文件給乾淨代理使用，目標是確認完整後端流程可以自己跑通：

```text
前端/外部 API 建帳與建單
-> Grok 主腦自動媒合
-> 產生 execution payload
-> 自動送 KeeperHub
-> 等 KeeperHub confirmed / failed
-> 後端依結果更新 DB
```

## 1. 測試目標

本測試要確認：

```text
1. public API 可以建立帳號、登入、建立買單、建立賣單
2. 訂單有 intent_json 與 signature
3. cactus-matching-loop 會自動呼叫 Grok 進行媒合
4. cactus-execution-reconciler 會自動送 KeeperHub
5. KeeperHub 回 confirmed 後，後端才更新買賣單 remaining_amount 與 status
6. 不會留下卡住的 proposed / dispatched execution
```

## 2. 前置條件

### VM 上需要有這些服務

```bash
systemctl is-active cactus-public-api cactus-internal-api cactus-execution-reconciler cactus-matching-loop nginx
```

預期輸出每一行都是：

```text
active
```

### 後端 `.env` 需要有

位置：

```text
/home/ark009770/CactusNetwork/backend/.env
```

至少需要：

```text
GROK_API_KEY=<Grok API key>
INTERNAL_API_TOKEN=<internal token>
KEEPERHUB_WEBHOOK_URL=https://app.keeperhub.com/api/workflows/o2o3h3yf8s6ps4ogg8h81/webhook
KEEPERHUB_API_TOKEN=<KeeperHub status API token>
ONCHAIN_PREFLIGHT_CHECKS=required
SP_TESTNET_RPC_URL=<Sepolia RPC URL>
INTENT_VAULT_ADDRESS=<IntentVault address>
SETTLEMENT_ROUTER_ADDRESS=<SettlementRouter address>
PROTOCOL_TREASURY_ADDRESS=<ProtocolTreasury address，actionType=2 才需要>
```

如果 KeeperHub status API 用 Authorization header，也可以用：

```text
KEEPERHUB_API_AUTHORIZATION=Bearer <KeeperHub status API token>
```

不要把 `.env` commit 到 GitHub。

鏈上預檢的目的：

```text
1. 送 KeeperHub 前先確認 IntentVault 小金庫裡有足夠 tokenIn
2. 確認 SettlementRouter.filledAmountIn 還沒有把該 intent 用完
3. actionType=2 時確認 ProtocolTreasury 有足夠 tokenOut
```

若這一步失敗，後端不會送 KeeperHub，也不會扣訂單量。這通常代表前端錢包還沒先把資產 deposit 進 IntentVault，或該 intent 已經被其他 execution 消耗。

### 本機需要有測試私鑰

測試私鑰放在本機：

```text
/Users/ansenchen/Downloads/私鑰.txt
```

檔案內需要至少 4 個 Sepolia 測試錢包私鑰。

不要把私鑰印出、貼到 log、或 commit。

## 3. 先跑基本健康檢查

在專案根目錄：

```bash
cd /Users/ansenchen/codex/CactusNetwork
```

本地後端測試：

```bash
/Users/ansenchen/codex/區塊鏈/.venv/bin/python -m pytest backend/tests -q
```

預期：

```text
99 passed
```

VM 後端測試：

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork && backend/.venv/bin/python -m pytest backend/tests -q"
```

預期：

```text
99 passed
```

VM 前端 build：

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork/frontend && npm run build"
```

預期：

```text
Compiled successfully
```

或看到 Next.js route build 完成。

## 4. 清理舊卡單

不要直接刪 DB。

先讓收尾器清掉舊的 proposed / dispatched execution：

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork/backend && . .venv/bin/activate && python -m scripts.execution_reconciler once --limit 50 --poll-interval-seconds 5 --max-wait-seconds 120 --timeout-seconds 60"
```

然後確認沒有卡住的 execution：

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork && python3 - <<'PY'
import sqlite3,json
conn=sqlite3.connect('backend/data/databases/executions.db')
conn.row_factory=sqlite3.Row
rows=[dict(r) for r in conn.execute(\"SELECT execution_id,status,sell_order_id,failure_reason FROM executions WHERE status IN ('proposed','dispatched') ORDER BY id\")]
print(json.dumps(rows,ensure_ascii=False,indent=2))
PY"
```

預期輸出：

```json
[]
```

## 5. 建立測試訂單

使用外部 public API：

```text
http://34.81.58.100
```

測試資料建議：

```text
賣單 1：賣 2.6 WETH，最低 2900 USDC，最多拆 3 單
賣單 2：賣 0.8 WETH，最低 2950 USDC，最多拆 2 單
買單 1：買 1.4 WETH，最高 3000 USDC，最多拆 2 單
買單 2：買 1.1 WETH，最高 2990 USDC，最多拆 2 單
買單 3：買 1.0 WETH，最高 2960 USDC，最多拆 2 單
```

每筆買賣單都必須帶：

```text
intent_json
signature
```

intent 使用 EIP-712 簽名，Sepolia token 建議：

```text
USDC = 0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238
WETH = 0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14
chainId = 11155111
fee = 100
priceLimit = 0
```

如果代理要自動建立測試資料，可以參考臨時測試腳本：

```text
/tmp/cactus_auto_services_flow.py
```

若該檔不存在，代理可依本節資料重建一個一次性腳本。腳本不能輸出私鑰。

## 6. 不要手動觸發媒合或 dispatch

建立訂單後，不要呼叫：

```text
POST /internal/matching/run
POST /internal/executions/{execution_id}/keeperhub/dispatch
POST /internal/executions/{execution_id}/result
```

全通測試要確認的是常駐服務自己處理：

```text
cactus-matching-loop
cactus-execution-reconciler
```

## 7. 觀察 DB 狀態

把剛建立的 `sellOrderId` 與 `buyOrderId` 填進查詢腳本。

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork && python3 - <<'PY'
import sqlite3,json

sell_ids=[13,14]
buy_ids=[17,18,19]
base='backend/data/databases'
out={}

conn=sqlite3.connect(base+'/sell_orders.db')
conn.row_factory=sqlite3.Row
out['sells']=[
    dict(r)
    for r in conn.execute(
        'SELECT id, amount, remaining_amount, status, attempts, operation_note FROM sell_orders WHERE id IN (%s) ORDER BY id' % ','.join('?'*len(sell_ids)),
        sell_ids,
    )
]

conn=sqlite3.connect(base+'/buy_orders.db')
conn.row_factory=sqlite3.Row
out['buys']=[
    dict(r)
    for r in conn.execute(
        'SELECT id, amount, remaining_amount, status, attempts, operation_note FROM buy_orders WHERE id IN (%s) ORDER BY id' % ','.join('?'*len(buy_ids)),
        buy_ids,
    )
]

conn=sqlite3.connect(base+'/executions.db')
conn.row_factory=sqlite3.Row
out['executions']=[
    dict(r)
    for r in conn.execute(
        'SELECT execution_id, status, sell_order_id, failure_reason, confirmed_at FROM executions WHERE sell_order_id IN (%s) ORDER BY id' % ','.join('?'*len(sell_ids)),
        sell_ids,
    )
]

print(json.dumps(out,ensure_ascii=False,indent=2))
PY"
```

每 15 秒查一次，最多等 10 分鐘。

## 8. 通過標準

### execution 通過標準

應看到至少一筆：

```text
status = confirmed
```

而且最後不應該有任何本批次 execution 停在：

```text
proposed
dispatched
```

查全域卡單：

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork && python3 - <<'PY'
import sqlite3,json
conn=sqlite3.connect('backend/data/databases/executions.db')
conn.row_factory=sqlite3.Row
rows=[dict(r) for r in conn.execute(\"SELECT execution_id,status,sell_order_id,failure_reason FROM executions WHERE status IN ('proposed','dispatched') ORDER BY id\")]
print(json.dumps(rows,ensure_ascii=False,indent=2))
PY"
```

預期：

```json
[]
```

### 訂單通過標準

成功媒合的賣單：

```text
remaining_amount 會下降
完全成交時 status = filled
operation_note 有 agent_matched 與 KeeperHub execution succeeded
```

成功媒合的買單：

```text
remaining_amount 會下降
完全成交時 status = filled
部分成交時 status = pending
operation_note 有 sell_order_id、filled_amount、unit_price_usdc
```

無法媒合的賣單：

```text
attempts 會增加
達上限後 status = invalid
operation_note 會記錄 agent_rejected 原因
```

## 9. 參考成功結果

最近一次成功全通測試結果：

```text
賣單 13：2.6 WETH 全部成交，status = filled
買單 17：成交 1.4，status = filled
買單 18：成交 1.1，status = filled
買單 19：成交 0.1，剩 0.9，status = pending
execution:13:match:1 = confirmed
execution:13:match:2 = confirmed
execution:13:match:3 = confirmed
全域 proposed / dispatched = []
```

## 10. 如果失敗要怎麼查

### 看服務狀態

```bash
ssh cactusnetwork "systemctl is-active cactus-public-api cactus-internal-api cactus-execution-reconciler cactus-matching-loop nginx"
```

### 看 Grok 媒合 log

```bash
ssh cactusnetwork "journalctl -u cactus-matching-loop -n 80 --no-pager"
```

常見問題：

```text
GROK_API_KEY 錯誤
Grok 回覆不是合法 JSON
candidateBuyOrders 為空
外部 Uniswap / DEX candidates 為空
```

### 看 KeeperHub 收尾 log

```bash
ssh cactusnetwork "journalctl -u cactus-execution-reconciler -n 80 --no-pager"
```

常見問題：

```text
KeeperHub webhook 沒收到
KeeperHub status API token 無效
KeeperHub 回 running 太久
KeeperHub 回 error / failed
本地訂單已 timeout，confirmed 無法套用，execution 會被標 failed
```

### 看 pending execution

```bash
ssh cactusnetwork "cd /home/ark009770/CactusNetwork/backend && . .venv/bin/activate && python -m scripts.execution_messages pending --limit 20"
```

重點看：

```text
readyForExecutor
missingFields
payload.intentA.signature
payload.routeDetails.matchedIntentB.signature
```

## 11. 測試時不能做的事

```text
不要印出私鑰
不要印出完整 .env
不要直接刪 production DB
不要手動把 execution 改 confirmed
不要手動扣 buy_orders / sell_orders remaining_amount
不要在 full flow 測試中手動呼叫 matching/run 或 keeperhub/dispatch
```

## 12. 測試完成後要回報

回報時至少包含：

```text
1. 本地 pytest 是否通過
2. VM pytest 是否通過
3. VM 前端 build 是否通過
4. 五個 systemd service 是否 active
5. 本次建立的 sellOrderId / buyOrderId
6. 本次產生的 executionId 與 final status
7. 最終 sell_orders / buy_orders remaining_amount 與 status
8. 是否還有 proposed / dispatched 卡單
9. 若失敗，貼出相關 journalctl 摘要，不要貼私鑰或 .env
```
