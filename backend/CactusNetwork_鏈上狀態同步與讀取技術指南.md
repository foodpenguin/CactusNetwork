# CactusNetwork 鏈上狀態同步與讀取技術指南

## 1. 什麼時機讀？(When to Read)

### 時機一：背景清理器 (Background Pruner) - 每 15~30 秒
* **執行者**：後端 Node.js / Python 伺服器。
* **動作**：定期掃描資料庫中所有 `Pending` 狀態的意圖。批次去鏈上查詢這些意圖的 `filledAmountIn` 與用戶的 `Vault Balance`。
* **目的**：提早將那些已經被完全成交，或是用戶已經把錢抽走的意圖標記為 `Invalid`，避免浪費 AI 的算力去分析無法執行的廢單。

### 時機二：決策前  - AI 運算的當下
* **執行者**：AI Agent (大腦)。
* **動作**：AI 在鎖定某幾個意圖，準備進行「暗池撮合」或「國庫內部化」前，**即時發出一次 RPC 讀取請求**。
* **目的**：確認國庫目前真的有錢 (Treasury Balance)，確認這兩個對手方的意圖目前確實還沒被搶走，確保決策所需的參數是 100% 正確的。

### 時機三：上鏈前模擬 - 發送交易前一刻
* **執行者**：負責發送交易的 KeeperHub。
* **動作**：拿到 AI 組裝好的 `executionData` 後，不要直接發送 `sendTransaction`，而是先呼叫 `eth_estimateGas` 或 `eth_call`。
* **目的**：這是最後一道防線，讓區塊鏈節點在記憶體中模擬跑一次這筆交易。如果模擬失敗 (Revert)，直接放棄發送，保證絕對不會浪費 Gas 手續費。

---

## 2. 具體要讀哪些資料？
透過 RPC 節點，主要需要讀取以下三個合約狀態：

### A. 用戶的金庫餘額
* **目標合約**：`IntentVault.sol`
* **呼叫方法**：`balances(address user, address token) view returns (uint256)`
* **判斷邏輯**：如果 `balances` < `intent.amountIn`，代表用戶餘額不足。AI 在決策時，`executeAmountIn` 絕對不能大於這個餘額，否則交易會失敗。

### B. 意圖已執行額度
* **目標合約**：`SettlementRouter.sol`
* **呼叫方法**：`filledAmountIn(bytes32 intentHash) view returns (uint256)`
* **判斷邏輯**：該意圖剩餘可執行的數量 = `intent.amountIn - filledAmountIn`。如果相減為 `0`，代表這筆意圖已經完美結算，可以直接從資料庫移除。

### C. 協議國庫的水位
* **目標合約**：目標代幣的 ERC20 合約 (例如 USDC)。
* **呼叫方法**：`balanceOf(address account) view returns (uint256)` (傳入 ProtocolTreasury 的地址)。
* **判斷邏輯**：如果 AI 想要走 `actionType == 2` (國庫提供流動性)，必須先確保國庫裡面的 USDC 餘額大於或等於 `treasuryAmountOut`。

---

## 3. 實作範例 (Ethers.js v6)

建議在 Node.js 中使用 `Promise.all` 同時並發查詢，以將網路延遲降到最低：

```javascript
const { ethers } = require("ethers");

// 設定 Provider (連接 Alchemy, Infura 或當地測試網)
const provider = new ethers.JsonRpcProvider("https://eth-sepolia.g.alchemy.com/v2/YOUR_KEY");

// 合約介面定義
const vaultAbi = ["function balances(address user, address token) view returns (uint256)"];
const routerAbi = ["function filledAmountIn(bytes32 intentHash) view returns (uint256)"];
const erc20Abi = ["function balanceOf(address owner) view returns (uint256)"];

const vaultContract = new ethers.Contract(VAULT_ADDRESS, vaultAbi, provider);
const routerContract = new ethers.Contract(ROUTER_ADDRESS, routerAbi, provider);
const tokenContract = new ethers.Contract(TOKEN_OUT_ADDRESS, erc20Abi, provider);

async function checkOnChainState(intent, intentHash) {
    // 使用 Promise.all 並發三個 RPC 請求，大幅減少等待時間
    const [vaultBalance, filledAmount, treasuryBalance] = await Promise.all([
        vaultContract.balances(intent.user, intent.tokenIn),
        routerContract.filledAmountIn(intentHash),
        tokenContract.balanceOf(TREASURY_ADDRESS)
    ]);

    const remainingAmount = BigInt(intent.amountIn) - BigInt(filledAmount);

    return {
        vaultBalance: vaultBalance.toString(),
        remainingAmount: remainingAmount.toString(),
        treasuryBalance: treasuryBalance.toString(),
        // 判斷這筆意圖是否還健康 (餘額足夠且還沒被完全執行)
        isValid: BigInt(vaultBalance) > 0n && remainingAmount > 0n
    };
}
```

### 相關的API key和資料

#### uniswap API KEY = 1AB5yS2uVMDwcYXP4q7zsVo0PE60lWEajnRYZKpCqOg

#### uniswap skill 
```bash
npx skills add uniswap/uniswap-ai --skill swap-integration
```

#### rpc api = 請在 `.env` 設定 `SEPOLIA_RPC_URL` 或 `SP_TESTNET_RPC_URL`

#### vault adress = 0xF1Defe986257b2e8A74f40A48dbe3673268709f4 

#### settlement router adress = 0x98d83435F4aBcE9AdC2C1635125e5f627b7d73E0

#### protocol treasury adress = 0x20E8fcF701F6C2CDd74263Fa43989a80c9627c6C

#### keeper address = 0x3219b06026e74d69f892a7ef87a542f882791615

#### 給 keeperHub的資料
```json
{
    "AI_to_Backend_Payload": {
        "description": "這是 AI Agent 經過思考後，回傳給的決策結果。後端會根據這包資料組裝 executionData",
        "// 1. 本次要執行的主意圖 (User A)": "",
        "intentA": {
            "intent": {
                "user": "0x1234567890abcdef1234567890abcdef12345678",
                "tokenIn": "0xWETH_ADDRESS...",
                "tokenOut": "0xUSDC_ADDRESS...",
                "amountIn": "1000000000000000000",
                "minAmountOut": "3000000000",
                "deadline": 1735689600,
                "salt": "0x1a2b3c4d5e...",
                "allowPartialFill": true
            },
            "signature": "0xdeadbeef..."
        },
        "// 2. AI 決定的執行策略": "",
        "actionType": 1, // 0: DEX, 1: 暗池 OTC, 2: 國庫提供流動性
        "executeAmountIn": "500000000000000000", // 本次 AI 決定從 intentA 扣除的金額 (支援部分成交)
        "// 3. 執行細節 (依據 actionType 有所不同，AI 只需要填寫對應的，其他的可以留 null)": "",
        "routeDetails": {
            "matchedIntentB": { // Type = 1 時使用，必須完整提供對手方的意圖、簽名，以及要扣除的數量
                "intent": {
                    "user": "0xUserB_Address...",
                    "tokenIn": "0xUSDC_ADDRESS...",
                    "tokenOut": "0xWETH_ADDRESS...",
                    "amountIn": "3000000000",
                    "minAmountOut": "1000000000000000000",
                    "deadline": 1735689600,
                    "salt": "0x9f8e7d6c5b...",
                    "allowPartialFill": true
                },
                "signature": "0xUserB_Signature...",
                "executeAmountInB": "1500000000" // AI 決定這次從 User B 扣除的金額
            },
            "treasuryAmountOut": null // Type = 2 時使用，例如 "1500000000" 表示國庫自掏腰包給用戶的數量
        }
    }
}
```
