# Blockchain Adapter Memory

## 外部資料來源

外部市場資料由 `blockchain_sync.py` 處理。第一版外部撮合走 Uniswap Trading API，並強制使用 V3。

## Uniswap V3 規則

- protocol 固定收斂為 `["V3"]`。
- 預設 chain id 是 Sepolia `11155111`。
- 預設 V3 pool fee 是 `100`。
- 預設 `sqrtPriceLimitX96` / price limit 是 `0`。
- 後端呼叫 Uniswap API 取得 quote。
- 後端自行組 `exactInputSingle` calldata。
- 不使用 Uniswap `/swap` 產生 calldata。
- 不在後端產生 permit signature。

## 外部 context

外部查詢結果會寫入：

- `external_contracts.db`
- `onchain_state.db`

回給主腦的 `externalContext.candidates[].candidate.reads` 可能包含：

- `amountIn`
- `amountOut`
- `Calldata`
- `v3Fee`
- `sqrtPriceLimitX96`
- quote / routing 相關欄位

主腦只有在看到可用 `Calldata` 時，才能輸出 `actionType = 0` 的外部 DEX payload。

## KeeperHub 邊界

主腦只輸出要送出的 payload。後端的 execution / message 模組負責把 payload 傳給 KeeperHub webhook，並等待 confirmed / failed 結果。確認結果回來前，不可把訂單視為成交。
