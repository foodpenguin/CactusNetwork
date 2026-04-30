## 階段一：蒐集資料 

1. **使用者意圖**
   * **來源**：後端資料庫。
   * **內容**：獲取目前市場上所有未過期 (`deadline > now`) 的活躍意圖 (`UserIntent`)。包含用戶想賣什麼 (`tokenIn`)、想買什麼 (`tokenOut`)、以及底線價格 (`minAmountOut`)。
2. **鏈上真實狀態**
   * **來源**：區塊鏈 RPC (例如 Infura / Alchemy)。
   * **內容**：檢查意圖是否已經被部分成交 (`filledAmountIn`)、用戶在金庫的真實餘額 (`Vault Balance`)，以及目前國庫 (`Protocol Treasury`) 可用的閒置流動性。
3. **外部市場行情**
   * **來源**：Uniswap API 與 Gas Tracker。
   * **內容**：取得目前市價的即時報價，以及預估上鏈的 Gas 成本，做為定價的基準。

---

## 階段二：評估與協商

### 密封競價池
* **核心概念**：後端 Mempool 成為一個「拍賣官」。
* **運作方式**：
  * 後端蒐集到意圖後，給所有 AI Agents 12 秒的時間思考。
  * 每個 AI 都要向後端盲交 (不公開) 他們願意給用戶的 `ExecutionAmountOut`。
  * Agent A 提議給用戶 2960 USDC。
  * Agent B 覺得自己演算法好，提議給用戶 2990 USDC。
  * Agent C 想要壟斷，提議給用戶 2995 USDC。
  * **結果**：12 秒一到，後端直接宣布 **Agent C 獲勝**，並只把 `executionData` 打包權利交給 Agent C。

### 策略 A：DEX 外部路由 (ActionType = 0)
* **評估**：如果走 Uniswap，扣掉 Gas 費與池子滑點後，用戶拿到的 `amountOut` 是否大於他的底線 `minAmountOut`？

### 策略 B：暗池 OTC 媒合與協商 (ActionType = 1)
* **具體評判標準 (數學條件)**：
  要讓 User A 與 User B 成功媒合，必須滿足以下兩個嚴格條件：
  1. **資產配對**：A 賣的代幣等於 B 買的代幣 (`A.tokenIn == B.tokenOut`)，且 A 買的代幣等於 B 賣的代幣 (`A.tokenOut == B.tokenIn`)。
  2. **價格重疊 (Price Overlap)**：雙方可接受的價格區間必須有交集。
     * User A 要求的最低匯率 = `A.minAmountOut / A.amountIn`
     * User B 願意支付的最高匯率 = `B.amountIn / B.minAmountOut`
     * 演算法條件：只有當 **`A.amountIn * B.amountIn >= A.minAmountOut * B.minAmountOut`** 時，雙方價格才有交集，OTC 交易才成立。
* **協商機制與定價**：
  * 當上述條件成立時，雙方的底線之間會產生一個「利潤空間 (Spread)」。
  * AI 會扮演「造市商」進行定價，通常是參考外部 Uniswap 即時市價，在雙方底線之間選定一個對雙方都公平的最終匯率 `R`。
  * 如果允許拆單 (`allowPartialFill = true`)，AI 可以只撮合雙方數量較小的那一方，把剩下的額度留到下一輪。
* **舉例說明**：
  * User A 想賣 1 WETH，底線是拿 2900 USDC。(A要求 1 WETH 至少換 2900)
  * User B 想賣 3100 USDC，底線是拿 1 WETH。(B願意 1 WETH 最多付 3100)
  * 評判標準成立 (`3100 >= 2900`)。AI 決定以市價 **1 WETH = 3000 USDC** 進行撮合。
  * **結果**：User A 拿到 3000 (高於底線 2900)，User B 花 3000 就買到 1 WETH (省下 100 USDC，這 100 USDC 結算時會因為 Router 的設計，自動退回 B 的 Vault 中)


### 策略 C：國庫內部化 (ActionType = 2)
* **評估利差**：如果市場上找不到 User B 來撮合，但 AI 發現外部 Uniswap 的價格是 1 WETH = 3000 USDC，而 User A 願意接受的最差價格 (底線) 是 2950 USDC。
* **內部化決策**：AI 會動用國庫的 USDC。它決定只給 User A 2999 USDC (仍滿足他的底線)。國庫收下了 1 WETH。


---

## 階段三：做出決策與輸出 

在評估完三種策略後，AI Agent 會進行最終的「利潤與勝率評分 (Scoring)」：

1. **打分標準**：
   * 優先選擇 **策略 B (OTC)**
   * 次要選擇 **策略 C (國庫)**
   * 最後才選 **策略 A (DEX)**，當作保底的流動性來源。
