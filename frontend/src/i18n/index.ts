export type Lang = 'zh' | 'en';

const zh = {
  navbar: {
    home: '首頁',
    trade: '交易',
    dashboard: '儀表板',
    connectWallet: '連接錢包',
  },
  landing: {
    badge: 'LLM 驅動 · OTC 暗池撮合',
    heroTitle: 'Zero Slippage.',
    heroSubtitle: 'AI 驅動。',
    heroTagline: '鏈上。',
    heroDesc: '專為巨鯨設計的 OTC 暗池流動性結算系統。無論多大的單，都能以市場最優價成交，滑價歸零。',
    statSavedLabel: '累積為用戶節省的滑價損失',
    statOrdersLabel: '已完成子單數',
    statSlippageLabel: '平均滑價',
    statOrdersSuffix: ' 筆',
    startTrading: '開始交易 →',
    sectionTitle: '兩層防護，徹底消滅滑價',
    features: [
      {
        title: 'TWAP 智能拆單',
        desc: '自動將大單切分為數十至數百筆子單，避免一次砸盤導致的滑價損失。',
      },
      {
        title: '暗池 OTC 撮合',
        desc: '在私有 P2P 網路中尋找對手方，訂單不暴露在公開 Mempool，防止被 MEV 機器人狙擊。',
      },
    ],
    ctaTitle: '準備好了嗎？',
    ctaDesc: '連接錢包，立刻提交您的第一筆大單。',
    ctaButton: '前往交易頁',
  },
  trade: {
    title: '交易',
    desc: '提交買單或賣單，系統將自動進行 TWAP 拆單與 OTC 暗池撮合。',
  },
  orderForm: {
    title: '下單',
    tabSell: '賣出 (Sell)',
    tabBuy: '買入 (Buy)',
    labelAsset: '資產',
    labelAmount: '數量 (WETH)',
    labelMinPrice: '最低單價 (USDC/WETH)',
    labelMaxPrice: '最高單價 (USDC/WETH)',
    labelMaxSplits: '最大拆單數',
    labelMaxFee: '最大手續費 (%)',
    amountPlaceholder: '例：1000',
    pricePlaceholder: '例：3000',
    estimateSlippage: '預估滑價',
    connectFirst: '請先連接錢包',
    submitting: '送出中...',
    submitSell: '提交賣單',
    submitBuy: '提交買單',
  },
  slippage: {
    highRisk: '[高風險]',
    warning: '[警告]',
    slippageMsg: (pct: string) => `滑價警告：直接執行將導致 ${pct}% 滑價`,
    protection: '本系統將啟動 TWAP 拆單防護，自動分批執行以保護您的成交價。',
  },
  agentTerminal: {
    running: '● 執行中',
    waiting: '> 等待訂單提交...',
    waitingDesc: '> 送出訂單後，Agent 日誌將在此即時顯示。',
  },
  progressCard: {
    chunkDone: (current: number, total: number) => `第 ${current}/${total} 筆完成`,
    saved: '節省',
    makerProfit: '做市商獲利',
  },
  dashboard: {
    title: '儀表板',
    desc: '追蹤您的訂單執行狀況與系統節省效益。',
  },
  summary: {
    savedLabel: '為用戶節省的滑價損失',
    makerLabel: '做市商累積獲利',
    ordersLabel: '完成訂單數',
  },
  ordersTable: {
    title: '訂單紀錄',
    empty: '尚無訂單。前往交易頁提交您的第一筆大單。',
    colDirection: '方向',
    colAsset: '資產',
    colAmount: '數量',
    colStatus: '狀態',
    colTime: '時間',
    statusPending: '處理中',
    statusConfirmed: '已完成',
    statusFailed: '失敗',
    localeTime: 'zh-TW',
  },
  priceChart: {
    title: '子單成交價走勢（零滑價驗證）',
    noData: '尚無成交資料',
    xLabel: '子單序號',
    tooltipChunk: (n: number) => `子單 #${n}`,
    tooltipPrice: '成交價',
    baseline: '基準價',
  },
  agentLogs: {
    build: (slippage: number, splits: number, chunkSize: number) => {
      const savings = (slippage * chunkSize * 3000 * splits * 0.01).toFixed(0);
      const makerProfit = (chunkSize * 3000 * 0.003).toFixed(2);
      return [
        `Agent A: 正在解析意圖... 呼叫 Uniswap API 獲取報價。`,
        `Agent A: 警告！直接執行將導致 ${slippage.toFixed(1)}% 滑價。啟動防護協議，拆分為 ${splits} 筆 x ${chunkSize} WETH。`,
        `Agent A: 向 UniDarkpool 廣播 ${chunkSize} WETH 賣單意圖。`,
        `Agent B: 攔截到意圖。在暗池中尋找對手方...`,
        `Agent B: 找到對手方，OTC 撮合成功，成交價優於市場。`,
        `Agent A: 執行 Tx [Swap ${chunkSize} WETH]... 成功 (滑價 0.00%)。`,
        `Dashboard: 第 1/${splits} 筆交易完成。目前節省 $${savings} USDC 滑價損失。做市商獲利 $${makerProfit} USDC。`,
      ];
    },
  },
};

const en = {
  navbar: {
    home: 'Home',
    trade: 'Trade',
    dashboard: 'Dashboard',
    connectWallet: 'Connect Wallet',
  },
  landing: {
    badge: 'LLM-Driven · OTC Dark Pool',
    heroTitle: 'Zero Slippage.',
    heroSubtitle: 'AI-Driven.',
    heroTagline: 'On-Chain.',
    heroDesc: 'OTC dark pool settlement built for whales. Execute orders of any size at the best market price with zero slippage.',
    statSavedLabel: 'Cumulative slippage saved for users',
    statOrdersLabel: 'Sub-orders completed',
    statSlippageLabel: 'Average slippage',
    statOrdersSuffix: '',
    startTrading: 'Start Trading →',
    sectionTitle: 'Two-Layer Protection. Zero Slippage.',
    features: [
      {
        title: 'TWAP Smart Splitting',
        desc: 'Automatically breaks large orders into dozens or hundreds of sub-orders to avoid slippage from market impact.',
      },
      {
        title: 'Dark Pool OTC Matching',
        desc: 'Finds counterparties in a private P2P network. Orders never appear in the public Mempool, preventing MEV bot attacks.',
      },
    ],
    ctaTitle: 'Ready to get started?',
    ctaDesc: 'Connect your wallet and submit your first large order now.',
    ctaButton: 'Go to Trade',
  },
  trade: {
    title: 'Trade',
    desc: 'Submit buy or sell orders. The system will automatically execute TWAP splitting and OTC dark pool matching.',
  },
  orderForm: {
    title: 'Place Order',
    tabSell: 'Sell',
    tabBuy: 'Buy',
    labelAsset: 'Asset',
    labelAmount: 'Amount (WETH)',
    labelMinPrice: 'Min Unit Price (USDC/WETH)',
    labelMaxPrice: 'Max Unit Price (USDC/WETH)',
    labelMaxSplits: 'Max Splits',
    labelMaxFee: 'Max Fee (%)',
    amountPlaceholder: 'e.g. 1000',
    pricePlaceholder: 'e.g. 3000',
    estimateSlippage: 'Estimate Slippage',
    connectFirst: 'Connect wallet first',
    submitting: 'Submitting...',
    submitSell: 'Submit Sell Order',
    submitBuy: 'Submit Buy Order',
  },
  slippage: {
    highRisk: '[HIGH RISK]',
    warning: '[WARNING]',
    slippageMsg: (pct: string) => `Slippage Alert: Direct execution would cause ${pct}% slippage`,
    protection: 'TWAP splitting protection will be activated to execute in batches and protect your fill price.',
  },
  agentTerminal: {
    running: '● Running',
    waiting: '> Waiting for order submission...',
    waitingDesc: '> Agent logs will appear here in real time after you submit an order.',
  },
  progressCard: {
    chunkDone: (current: number, total: number) => `Chunk ${current}/${total} done`,
    saved: 'Saved',
    makerProfit: 'Maker profit',
  },
  dashboard: {
    title: 'Dashboard',
    desc: 'Track your order execution and system savings.',
  },
  summary: {
    savedLabel: 'Slippage saved for users',
    makerLabel: 'Cumulative maker profit',
    ordersLabel: 'Orders completed',
  },
  ordersTable: {
    title: 'Order History',
    empty: 'No orders yet. Go to the Trade page to submit your first large order.',
    colDirection: 'Direction',
    colAsset: 'Asset',
    colAmount: 'Amount',
    colStatus: 'Status',
    colTime: 'Time',
    statusPending: 'Pending',
    statusConfirmed: 'Confirmed',
    statusFailed: 'Failed',
    localeTime: 'en-US',
  },
  priceChart: {
    title: 'Sub-Order Fill Price Trend (Zero Slippage Proof)',
    noData: 'No fill data yet',
    xLabel: 'Sub-order #',
    tooltipChunk: (n: number) => `Chunk #${n}`,
    tooltipPrice: 'Fill Price',
    baseline: 'Base Price',
  },
  agentLogs: {
    build: (slippage: number, splits: number, chunkSize: number) => {
      const savings = (slippage * chunkSize * 3000 * splits * 0.01).toFixed(0);
      const makerProfit = (chunkSize * 3000 * 0.003).toFixed(2);
      return [
        `Agent A: Parsing intent... Calling Uniswap API to fetch quotes.`,
        `Agent A: WARNING! Direct execution would cause ${slippage.toFixed(1)}% slippage. Activating protection — splitting into ${splits} chunks x ${chunkSize} WETH.`,
        `Agent A: Broadcasting ${chunkSize} WETH sell intent to UniDarkpool.`,
        `Agent B: Intent intercepted. Searching for counterparty in dark pool...`,
        `Agent B: Counterparty found. OTC match successful, fill price beats market.`,
        `Agent A: Executing Tx [Swap ${chunkSize} WETH]... Success (slippage 0.00%).`,
        `Dashboard: Chunk 1/${splits} complete. Saved $${savings} USDC slippage. Maker profit $${makerProfit} USDC.`,
      ];
    },
  },
};

export const translations: Record<Lang, typeof zh> = { zh, en };
export type Translations = typeof zh;
