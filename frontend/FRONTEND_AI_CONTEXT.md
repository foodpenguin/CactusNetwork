# CactusNetwork Frontend — AI Context & Instructions

你正在協助開發 **CactusNetwork** 的 Next.js 前端。
這份文件是你唯一需要的背景資料，請完整閱讀後再動手。

---

## 專案目標

CactusNetwork 是一個 **LLM 驅動的 OTC + JIT 流動性結算系統**，核心價值：
- 巨鯨大單（例如 1000 WETH）在 DEX 直接交易會產生 ~5% 滑價
- 本系統透過 TWAP 拆單 + 暗池撮合 + Uniswap V3 JIT 流動性，將滑價降至 0%
- Agent A（執行者）負責拆單與廣播；Agent B（造市商）負責 JIT 注入與結算

前端的任務是讓使用者能夠提交買/賣單，並即時觀察 Agent 運作過程。

---

## 設計規範

**風格**：現代簡約 DeFi，以奶油白為基調、粉紅仙人掌為點綴，質感介於 Uniswap 的清爽與 Zora 的品牌感之間  
**Logo 色系**（從 logo.png 萃取）：
- 背景色：奶油白 `#faf5f0`
- 主粉紅（仙人掌主體）：`#f2a8b4`
- 深粉紅（accent / hover）：`#e07585`
- 標題文字：近黑 `#1c1c1c`
- 副標 "network" 文字：粉紅 `#e07585`
- 邊框 / 分隔線：`#e8ddd5`

**頁面背景**：奶油白 `#faf5f0`（淺色模式為主，不做深色模式）  
**卡片 / 面板**：純白 `#ffffff`，帶 `box-shadow: 0 2px 12px rgba(0,0,0,0.06)`  
**按鈕主色**：粉紅 `#f2a8b4`，hover 加深至 `#e07585`，文字色 `#1c1c1c`  
**狀態色**：
- 成功 / confirmed：綠 `#4caf7d`
- 警告 / pending：琥珀 `#f5a623`
- 危險 / failed / 高滑價：紅 `#e05555`

**AgentTerminal 終端機**：例外使用深色背景 `#1c1c1c`，字型粉紅 `#f2a8b4`（呼應品牌色），等寬字型  
**字體**：`Inter`（標題）/ `DM Sans`（內文）/ `JetBrains Mono`（終端機）  
**Logo 檔案**：`/logo.png`（粉紅仙人掌，奶油白底，圓潤造型）

---

## 技術棧（固定，不要更換）

| 層次 | 套件 |
|---|---|
| 框架 | Next.js 14+ App Router |
| 語言 | TypeScript |
| 樣式 | Tailwind CSS + shadcn/ui |
| 錢包 | wagmi v2 + RainbowKit |
| 資料 | TanStack Query v5 |
| 即時通訊 | WebSocket（優先）/ 模擬動畫（fallback）|
| 圖表 | Recharts |

---

## 目錄結構（請照此建立）

```
src/
├── app/
│   ├── layout.tsx          # 全域 layout，含 Providers
│   ├── page.tsx            # Landing Page (/)
│   ├── trade/
│   │   └── page.tsx        # 主交易頁 (/trade)
│   └── dashboard/
│       └── page.tsx        # 儀表板 (/dashboard)
├── components/
│   ├── layout/
│   │   ├── Navbar.tsx
│   │   └── Footer.tsx
│   ├── trade/
│   │   ├── OrderForm.tsx         # Buy/Sell 下單表單
│   │   ├── SlippageWarning.tsx   # 滑價警告 Banner
│   │   ├── AgentTerminal.tsx     # 即時 Agent log 終端機
│   │   └── OrderProgressCard.tsx # N/100 進度卡片
│   └── dashboard/
│       ├── SavingsSummary.tsx    # 省了多少 USDC
│       ├── ActiveOrdersTable.tsx # 訂單列表
│       └── PriceChart.tsx        # 成交價走勢圖
├── lib/
│   ├── api.ts              # 全域 fetch client（自動帶 Bearer token）
│   ├── auth.ts             # register + login 邏輯
│   └── wagmi.ts            # wagmi / RainbowKit config
├── hooks/
│   ├── useAuth.ts          # 錢包連線後自動 register & login
│   ├── useOrders.ts        # 買單/賣單 mutation
│   └── useAgentLog.ts      # WebSocket log stream
└── types/
    └── api.ts              # 所有 API request/response 型別
```

---

## API 規格（後端已存在，請嚴格對應）

### Base URL
```
公開 API：http://127.0.0.1:8000
內部 API：http://127.0.0.1:8001  （前端不使用）
```

### 1. 建立帳號 `POST /accounts`
```ts
// Request
{ account_name: string, password: string, public_key: string }

// Response
{ message: string, accountName: string, publicKey: string, accountLevel: string, day: number, createdAt: string }
```
> 注意：不可傳 `account_level` 或 `day`，這兩個是後台欄位。

### 2. 登入 `POST /login`
```ts
// Request
{ account_name: string, password: string }

// Response
{ message: string, tokenType: "Bearer", accessToken: string, expiresAt: string }
```
> 拿到 `accessToken` 後，所有後續請求 header 加 `Authorization: Bearer <accessToken>`。

### 3. 建立買單 `POST /buy-orders`
```ts
// Header: Authorization: Bearer <accessToken>
// Request
{
  asset: "WETH",
  amount: number,
  max_unit_price_usdc: number,
  max_splits: number,         // 最多拆幾單
  max_fee_percent: number,    // 例如 0.3 代表 0.3%

  // 可選（有帶才能直接上鏈）
  intent_json?: {
    user: string, tokenIn: string, tokenOut: string,
    amountIn: string, minAmountOut: string,
    deadline: number, salt: string, allowPartialFill: boolean
  },
  signature?: string
}

// Response
{ buyOrderId: number, status: "pending", hasIntent: boolean, hasSignature: boolean, ... }
```

### 4. 建立賣單 `POST /sell-orders`
```ts
// Header: Authorization: Bearer <accessToken>
// Request
{
  asset: "WETH",
  amount: number,
  min_unit_price_usdc: number,
  max_splits: number,
  max_fee_percent: number,

  // 可選（同買單）
  intent_json?: { ... },
  signature?: string
}

// Response
{ sellOrderId: number, status: "pending", hasIntent: boolean, hasSignature: boolean, ... }
```

### intent_json + signature 產生方式
使用 wagmi 的 `signTypedData`，EIP-712 domain 與 types 由後端定義（待確認）。  
如果使用者錢包無法簽名，不帶這兩個欄位也可建立訂單，後端會標記 `incomplete_missing_frontend_wallet_fields`。

---

## 認證流程（前端核心邏輯）

```
使用者點 Connect Wallet (RainbowKit)
  ↓
取得 walletAddress (e.g. 0xabc...def)
  ↓
account_name = 地址前 6 碼 + 後 4 碼，例如 "0xabc1...ef12"
password = 地址本身（固定，無需使用者輸入）
public_key = 地址本身
  ↓
嘗試 POST /login → 若失敗（帳號不存在）→ POST /accounts → 再 POST /login
  ↓
將 accessToken 存入 React Context / localStorage
```

---

## 頁面說明

### `/` Landing Page
- Navbar（Logo + Connect Wallet 按鈕）
- Hero Section
  - 標題：「Zero Slippage. AI-Driven. On-Chain.」
  - 副標：「LLM 驅動的 OTC + JIT 流動性結算，為巨鯨大單消除滑價。」
  - 動畫數字：已累積為用戶節省 $X USDC 滑價損失
  - CTA 按鈕：「Start Trading →」→ 導向 `/trade`
- 三格特色卡片：TWAP 拆單 / 暗池撮合 / JIT 零滑價

### `/trade` 主交易頁
版面：左側 OrderForm（40%）+ 右側 AgentTerminal（60%）

**OrderForm**
- Buy / Sell tab 切換
- 欄位：Asset（固定 WETH/USDC）、Amount、單價限制、Max Splits、Max Fee %
- 「預估滑價」按鈕（呼叫 Uniswap Quoter 或模擬計算）
- 若預估滑價 > 1%，顯示 `SlippageWarning`（黃色警告）；> 3% 顯示紅色
- SlippageWarning 內容：「直接執行將導致 X% 滑價，本系統將自動拆分為 N 筆保護您的成交價。」
- 送出按鈕：「Submit Order」→ 呼叫 API，成功後 AgentTerminal 開始顯示 log

**AgentTerminal**
- 外觀：黑色背景、等寬字型、綠色文字（`#4caf50`）
- 頂部標題列：`● CactusNetwork Agent Console`
- 顯示格式：
  ```
  [12:34:56] Agent A: 正在解析意圖... 呼叫 Uniswap API 獲取報價。
  [12:34:57] Agent A: 警告！直接執行將導致 4.8% 滑價。啟動防護協議，拆分為 100 筆 x 10 WETH。
  [12:34:58] Agent A: 向 UniDarkpool 廣播 10 WETH 賣單意圖。
  [12:34:59] Agent B: 攔截到意圖。計算最優 JIT 區間為 Tick 201310 ~ 201330。
  [12:35:00] Agent B: 觸發 KeeperHub deploy_JIT_liquidity。
  [12:35:01] Agent A: 執行 Tx [Swap 10 WETH]... 成功 (滑價 0.00%)。
  [12:35:02] KeeperHub: 執行 Burn & Collect... 成功。
  ```
- 若後端無 WebSocket，使用 `useAgentLog` hook 的模擬模式（setTimeout 逐行顯示）
- 底部顯示 `OrderProgressCard`：進度條 + 「第 N/100 筆完成 · 已節省 $X USDC」

### `/dashboard` 儀表板
- 頂部 `SavingsSummary`：
  - 左：「為巨鯨節省 $1,500 USDC」
  - 右：「做市商獲利 $9 USDC」
- `ActiveOrdersTable`：
  - 欄位：Order ID / 方向 / Asset / 金額 / 狀態徽章 / 時間
  - 狀態徽章顏色：pending=黃、confirmed=綠、failed=紅
- `PriceChart`（Recharts LineChart）：
  - X 軸：子單序號（1～100）
  - Y 軸：成交單價（USDC）
  - 預期結果：幾乎水平的一條線（零滑價證明）

---

## AgentTerminal 模擬 Log 腳本

當後端 WebSocket 尚未就緒時，送出訂單後按此順序播放（每行間隔 800ms）：

```ts
const SIMULATED_LOGS = [
  "Agent A: 正在解析意圖... 呼叫 Uniswap API 獲取報價。",
  "Agent A: 警告！直接執行將導致 {slippage}% 滑價。啟動防護協議，拆分為 {splits} 筆 x {chunkSize} WETH。",
  "Agent A: 向 UniDarkpool 廣播 {chunkSize} WETH 賣單意圖。",
  "Agent B: 攔截到意圖。呼叫 Uniswap Pool API，計算最優 JIT 區間為 Tick 201310 ~ 201330。",
  "Agent B: 觸發 KeeperHub MCP Server 工具 deploy_JIT_liquidity。",
  "KeeperHub: JIT 流動性已部署，等待 Swap 觸發...",
  "Agent A: 執行 Tx [Swap {chunkSize} WETH]... 成功 (完美命中 JIT 區間，滑價 0.00%)。",
  "KeeperHub: 執行 Burn & Collect... 成功。",
  "Dashboard: 第 {n}/{splits} 筆交易完成。目前節省 ${savings} USDC 滑價損失。做市商獲利 ${makerProfit} USDC。",
];
```

---

## 重要約束

1. **不要直接呼叫內部 API**（port 8001）；那是給區塊鏈端用的。
2. **account_level 與 day 欄位**不可由前端傳入。
3. **payload 欄位**（intentA、actionType 等）是後端輸出給區塊鏈用的，前端不需要處理。
4. 前端只需 4 支 API：`POST /accounts`、`POST /login`、`POST /buy-orders`、`POST /sell-orders`。
5. 所有需要認證的 API，`Authorization: Bearer <token>` header 必須由全域 client 自動注入。
6. `intent_json` 與 `signature` 欄位為可選，有帶最好（讓訂單可直接上鏈），沒帶也能成立。

---

## Open Questions（實作前需確認）

1. 後端是否有 WebSocket / SSE endpoint 提供 Agent log stream？路徑是？
2. `signTypedData` 的 EIP-712 domain 與 types 定義為何？（需後端提供）
3. Uniswap 滑價預估要前端直接呼叫 Quoter 合約，還是後端有代查 API？
