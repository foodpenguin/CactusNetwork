# CactusNetwork

> Intent-driven DeFi execution on autopilot — powered by EIP-712 signatures, automated OTC matching, and smart routing.

## The Problem
DeFi trading often forces users to choose between high slippage on DEXs, MEV attacks on public mempools, or the complexity of manually splitting large orders. When users execute large trades, they face price impact and sandwich attacks. Most users lack the tools to perform TWAP (Time-Weighted Average Price) execution, dark pool routing, or optimal DEX splitting manually.

The result: Traders lose significant value to slippage and MEV extractors on every large on-chain swap.

## The Solution
CactusNetwork is an intent-driven DeFi platform that takes the complexity out of trade execution. You simply state your intent (what you want to buy/sell and your price limits), and CactusNetwork's autonomous agents handle the rest — splitting, routing, and executing your trade for the best possible price.

Here's how:

1. **You connect your wallet** — seamless Web3 authentication using EIP-191 `personal_sign` (no passwords).
2. **You state your intent and sign** — deposit funds into the `IntentVault` and sign an EIP-712 typed data signature detailing your swap conditions (asset, amount, limit price, max splits). 
3. **CactusNetwork watches and acts** — our backend agents monitor your intents, compare routes across internal OTC dark pools, protocol treasury liquidity, and public DEXs (like Uniswap V3), and execute the optimal sequence of transactions on your behalf.

You stay in control. Funds remain in the `IntentVault` until execution, protected by your EIP-712 signature conditions.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      User's Wallet                      │
│                                                         │
│  ┌─────────────────┐    ┌────────────────────────────┐  │
│  │  EIP-191 Sign   │───▶│   Authentication (Login)   │  │
│  └─────────────────┘    └────────────────────────────┘  │
│                                                         │
│  ┌─────────────────┐    ┌────────────────────────────┐  │
│  │  EIP-712 Sign   │───▶│   IntentVault Deposit &    │  │
│  │  (Trade Intent) │    │   Order Submission         │  │
│  └─────────────────┘    └────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
            │
            │ Signed Intent JSON
            ▼
┌─────────────────────────────────────────────────────────┐
│               CactusNetwork Backend                     │
│                                                         │
│  API Server (FastAPI)                                   │
│  ├── POST /login         — Wallet verification          │
│  ├── POST /buy-orders    — Receive signed buy intent    │
│  ├── POST /sell-orders   — Receive signed sell intent   │
│  └── POST /account/upgrade — Verify subscription fee    │
│                                                         │
│  Agent Runner & Execution Engine                        │
│  ├── Parse intents and fetch Uniswap V3 Quotes          │
│  ├── Match via internal OTC Dark Pool                   │
│  ├── Leverage Protocol Treasury Liquidity               │
│  └── Split large orders (TWAP protection)               │
│                                                         │
│  Settlement Router                                      │
│  └── Dispatch optimal calldata to on-chain contracts    │
└─────────────────────────────────────────────────────────┘
            │
            │ On-chain Execution
            ▼
┌─────────────────────────────────────────────────────────┐
│                 Smart Contracts                         │
│  ├── IntentVault.sol     (Escrows user funds)           │
│  ├── SettlementRouter.sol(Verifies signature & swaps)   │
│  └── PriorityFee.sol     (Subscription payments)        │
└─────────────────────────────────────────────────────────┘
```

1. **Deposit & Sign** — Users approve and deposit WETH/USDC into the `IntentVault`, then sign their trading parameters.
2. **Evaluate & Route** — The backend uses the Uniswap Trading API to fetch public DEX quotes, compares them against internal dark pool orders, and checks if the Protocol Treasury can provide better liquidity.
3. **Execute** — The `SettlementRouter` contract receives the EIP-712 signature and the optimized calldata. It verifies the signature, enforces price limits, and executes the swap, ensuring the user gets the exact outcome they signed for.

## Security Model
CactusNetwork protects your funds through strict cryptographic verification:

| Layer | What it blocks | How |
|-------|---------------|-----|
| **Nonce-based Auth** | Replay attacks | EIP-191 `personal_sign` challenges with 5-minute expiring nonces ensure only the wallet owner can log in. |
| **EIP-712 Signatures** | Unauthorized execution | User funds in the `IntentVault` can only be moved if the `SettlementRouter` receives a valid EIP-712 signature matching the exact swap parameters. |
| **Price Limit Enforcement** | Slippage & MEV | The smart contract strictly enforces the `minAmountOut` specified in the user's signed intent, blocking any execution that results in high slippage. |
| **Tiered Access** | Service abuse | Subscription tiers (Free, Plus, Max) managed via the `PriorityFee` contract ensure high-priority execution and treasury access are properly metered. |

## Tiered Subscription System
CactusNetwork features an on-chain subscription model powered by the `PriorityFee` contract. Users can pay USDC to upgrade their accounts:
- **Free:** Basic order functionality.
- **Plus ($20 USDC/mo):** Priority order matching and treasury liquidity support.
- **Max ($60 USDC/mo):** Highest priority matching, increased max order splits, and VIP dedicated support.

Account levels are seamlessly verified on the backend by tracking `PriorityFee.pay()` transaction hashes.

## Deployed Contracts (Sepolia Testnet)
| Contract | Address |
|----------|---------|
| IntentVault | `0xF1Defe986257b2e8A74f40A48dbe3673268709f4` |
| SettlementRouter | `0x98d83435F4aBcE9AdC2C1635125e5f627b7d73E0` |
| PriorityFee | `0xbF57d7f6d829A647F880BBE18bbEF8e66DC15C61` |
| USDC (Mock) | `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238` |
| WETH (Mock) | `0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14` |

## Project Structure
```
CactusNetwork/
├── foundry/      # Smart Contracts (IntentVault, SettlementRouter, PriorityFee)
├── backend/      # Python FastAPI — API server, intent matching, Agent execution engine
└── frontend/     # Next.js (React) — Web3 wallet login, Order interface, Dashboard with Etherscan links
```

## Getting Started

### Prerequisites
- Node.js 18+ (for frontend)
- Python 3.10+ (for backend)
- Foundry (for smart contracts)

### Frontend Setup
```bash
cd frontend
pnpm install
pnpm run dev     # starts on http://localhost:3000
```

### Backend Setup
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn scripts.api_server:app --reload --port 8000
```

*Note: The backend requires a Uniswap API key configured in a `.env` file to fetch live quotes.*

## License
MIT
