# Developer Experience Feedback - CactusNetwork

**Team / Project:** CactusNetwork  
**Submitted By:**[你的名字 / 聯絡信箱或 GitHub 帳號]  
**Tools Used:** Uniswap Trading API (Routing/Quotes), KeeperHub, Developer Platform  

This file contains our specific and actionable feedback regarding the developer experience (DX) of building with the Uniswap API and KeeperHub, fulfilling the hackathon submission requirements.

---

## 1. UX/UI & Developer Friction (KeeperHub & Dev Platform)
*(What was confusing, slow, or unclear when using the tools?)*

* **Friction 1: [在此寫下一個痛點，例如：KeeperHub Dashboard 缺少狀態過濾器]**
  * **Issue:** When submitting multiple automated execution payloads from our Orchestrator to KeeperHub, the UI dashboard became cluttered. It was hard to distinguish between pending, successful, and failed transactions quickly.
  * **Actionable Suggestion:** Please add a status filter (e.g., dropdown for `Pending`, `Failed`, `Success`) and a search bar for `txHash` or `Payload ID` on the KeeperHub dashboard.
* **Friction 2:[填寫第二個痛點，例如：API Rate Limit 報錯不夠明確]**
  * **Issue:**[描述你遇到的不順暢之處，例如：「當達到 API 速率限制時，回傳的錯誤沒有包含 Retry-After header，導致我們的系統不知道該等多久」。]

---

## 2. Reproducible Bugs
*(Issues with clear steps to replicate)*

* **Bug 1:[在此寫下一個 Bug，例如：/v1/quote 回傳的 calldata 在特定代幣對會報錯]**
  * **Description:** The `/v1/quote` endpoint returns invalid calldata when swapping a standard ERC20 token to a fee-on-transfer token with exact output routing.
  * **Steps to Reproduce:**
    1. Call `POST /v1/quote` with `tokenIn` = `[填寫代幣地址]`, `tokenOut` = `[填寫代幣地址]`.
    2. Set `amountOut` to `1000000000000000000`.
    3. Take the returned `calldata` and simulate it via `eth_call` or Tenderly.
  * **Expected Result:** The simulation should pass and quote the exact output.
  * **Actual Result:** The transaction reverts with `[填寫錯誤代碼，如: Error: STF]`.

* **Bug 2: [如果有 KeeperHub 的 Bug 可以寫在這裡]**
  * **Description:** [描述 Bug]
  * **Steps to Reproduce:**
    1. [步驟 1]
    2. [步驟 2]
    3.[步驟 3]

---

## 3. Documentation Gaps
*(Where the docs left us stuck)*

* **Gap 1: [在此寫下文件漏掉的東西，例如：Slippage 參數的單位未標示]**
  * **Issue:** In the Uniswap API Routing documentation, the `slippageTolerance` parameter is mentioned, but the format/unit is not defined. We had to guess whether 1% should be written as `1`, `0.01`, or basis points (`100`). 
  * **Actionable Suggestion:** Update the API reference to explicitly state the expected format for `slippageTolerance` (e.g., "float, where 0.01 equals 1%").
* **Gap 2: [ KeeperHub 整合文件的缺失 ]**
  * **Issue:**[描述你整合 KeeperHub 時哪裡看不懂，例如：「文件中沒有提供 Python 的 Payload 簽名範例，只有 TypeScript 範例，導致我們花了 3 小時自己刻 Python 的實作」。]

---

## 4. Feature Requests & Missing Endpoints
*(What’s missing that would have made the build easier)*

* **Request 1:[例如：需要歷史成交價 API]**
  * **Context:** Our Grok AI Agent needs to evaluate if the current quote from Uniswap is significantly worse than the 5-minute historical average to prevent executing during extreme volatility.
  * **Missing Feature:** There is no endpoint in the Uniswap Trading API to fetch the recent TWAP (Time-Weighted Average Price) or historical trades for a specific pool.
  * **Why it matters:** Adding a `/v1/history` or `/v1/twap` endpoint would allow autonomous agents to make safer routing decisions without relying on third-party data providers.

* **Request 2: [例如：KeeperHub Webhook 通知]**
  * **Context:** Currently, our Orchestrator has to constantly poll the status of our submitted transactions.
  * **Missing Feature:** Webhooks for KeeperHub payload status updates (`onSuccess`, `onRevert`).

---

## 5. What Worked Well (Positive Feedback)
*(Briefly mention what was actually good so they know what not to change!)*

* **Uniswap Quote Latency:** The response time for fetching DEX quotes was exceptionally fast, allowing our Grok Agent to make real-time decisions without timing out.
* **KeeperHub Abstraction:** Passing the execution payload to KeeperHub significantly reduced our smart contract complexity, as we didn't have to manage gas bumps or RPC node failures on our end.