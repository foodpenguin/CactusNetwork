# Orchestrator Memory

## 中控定位

中控是函式庫，不是主腦。它負責讀取資料庫、準備任務、套用主腦決策、管理 execution 狀態與訂單隊列。

## 任務準備流程

`prepare_agent_task()` 會：

- 刷新 timeout 狀態。
- 從 `sell_orders.db` 選出下一筆 pending 賣單。
- 從 `buy_orders.db` 找出符合基本規則的候選買單。
- 產生包含 `sellOrder`、`candidateBuyOrders`、`matchingRule` 的 task。

## 本地候選規則

本地買單成為候選需符合：

- 買單狀態是 `pending`。
- 買單 `remaining_amount > 0`。
- 買單 `asset == sellOrder.asset`。
- 買單 `max_unit_price_usdc >= sellOrder.min_unit_price_usdc`。
- 買單沒有被尚未完成的 execution 鎖住。

## 決策套用規則

`apply_agent_decision()` 只接受：

- `proposed_execution`
- `request_external_contract_data`
- `rejected`
- `invalid`

`proposed_execution` 只會建立 execution，不會直接扣訂單。只有 `confirm_execution()` 收到 confirmed 後，才會扣除 `remaining_amount` 並更新訂單狀態。

## 外部查詢規則

`request_external_contract_data` 只記錄主腦要求外部資料，不改變訂單隊列。runner 會在收到這個狀態後呼叫外部同步模組，然後把 `externalContext` 再交回主腦做第二次決策。
