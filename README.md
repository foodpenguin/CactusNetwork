# CactusNetwork

> Intent-driven DeFi execution on autopilot — powered by EIP-712 signatures, automated OTC matching, AI-driven routing (Grok), and KeeperHub.

---

## The Problem
DeFi trading often forces users to choose between high slippage on DEXs, MEV attacks on public mempools, or the complexity of manually splitting large orders. When users execute large trades, they face price impact and sandwich attacks. Most users lack the tools to perform TWAP (Time-Weighted Average Price) execution, dark pool routing, or optimal DEX splitting manually.

The result: Traders lose significant value to slippage and MEV extractors on every large on-chain swap.

## The Solution & Our Approach
CactusNetwork is an intent-driven DeFi platform that takes the complexity out of trade execution. You simply state your intent (what you want to buy/sell and your price limits), and our **hybrid AI-evaluation architecture (Orchestrator + Grok Main Agent)** handles the rest. 

Our system strictly prioritizes **Local OTC matching (Dark Pool)** first. If no match is found, it queries external liquidity (Uniswap API) and ultimately dispatches the optimized transaction on-chain using **KeeperHub**.

1. **You connect your wallet** — seamless Web3 authentication using EIP-191 `personal_sign`.
2. **You state your intent and sign** — deposit funds into the `IntentVault` and sign an EIP-712 typed data signature.
3. **CactusNetwork Agent Engine watches and acts** — the Orchestrator prepares tasks, the Grok AI Agent evaluates the best route (OTC vs. Uniswap), and passes the payload to KeeperHub for execution.

## Architecture & Agent Evaluation Flow

Our backend employs a hybrid architecture to prevent AI hallucinations while maximizing routing efficiency. 

```text
┌─────────────────────────────────────────────────────────┐
│                      User's Wallet                      │
│  ┌─────────────────┐    ┌────────────────────────────┐  │
│  │  EIP-712 Sign   │───▶│   IntentVault Deposit &    │  │
│  │  (Trade Intent) │    │   Order Submission         │  │
│  └─────────────────┘    └────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
            │ Signed Intent JSON
            ▼
┌─────────────────────────────────────────────────────────┐
│               CactusNetwork Backend                     │
│                                                         │
│  1. Orchestrator (Pre-screening & Task Prep)            │
│  ├── Fetches pending Sell Orders                        │
│  └── Filters candidate Buy Orders (price, asset, lock)  │
│                           │                             │
│  2. Grok Main Agent (Decision Brain)                    │
│  ├── [State 1] Local OTC First (Matches dark pool)      │
│  ├── [State 2] Request External Data (Asks Uniswap API) │
│  ├── [State 3] External DEX Route (Verifies slippage)   │
│  └── [State 4] Rejected/Invalid (Safety abort)          │
│                           │                             │
│  3. Execution Payload Generation                        │
└─────────────────────────────────────────────────────────┘
            │ actionType, routeDetails, signature
            ▼
┌─────────────────────────────────────────────────────────┐
│                    KeeperHub                            │
│  ├── Receives AI-proposed payload                       │
│  └── Dispatches optimal calldata to on-chain contracts  │
└─────────────────────────────────────────────────────────┘
            │ On-chain Execution
            ▼
┌─────────────────────────────────────────────────────────┐
│                 Smart Contracts (Sepolia)               │
│  ├── IntentVault.sol / SettlementRouter.sol             │
└─────────────────────────────────────────────────────────┘
```

### Deep Dive: AI Agent Evaluation (Grok + Orchestrator)

* **Task Preparation (Orchestrator Memory):** The Orchestrator acts as the central hub. It picks a pending sell order and pre-filters local candidate buy orders. The conditions are strict: same asset, buyer's `max_unit_price >= seller's min_unit_price`, and the order must not be locked.
* **Main Agent Decision Logic (Grok AI):** The Grok agent receives the filtered candidates and optional `externalContext`. It evaluates strictly in this order:
  1. **Local OTC First:** If a local candidate matches, it generates a `proposed_execution` for an OTC settlement.
  2. **Request External Data:** If no local match exists and there's no Uniswap data yet, the Agent outputs `request_external_contract_data` (asking the Orchestrator to fetch quotes via Uniswap API).
  3. **External DEX Execution:** Once the Orchestrator feeds the Uniswap V3 calldata back, the Agent does a second-pass evaluation. If slippage conditions are met, it outputs `proposed_execution` with DEX routing.
  4. **Rejected/Invalid:** If conditions fail or data is missing, the Agent outputs `rejected` to prevent loss of funds.
* **Safety & Constraints:** The AI Agent never directly deducts funds. It only outputs a standardized `executionPayload` (intent, signature, routeDetails). The Agent is explicitly prompted against forging hashes or addresses.

### How KeeperHub is Used

Once the Grok Main Agent successfully formulates a valid `proposed_execution` (either OTC or DEX routing), the Orchestrator packages this JSON payload. Instead of managing gas, nonces, and RPC nodes manually, our backend forwards this payload directly to **KeeperHub**.

KeeperHub acts as our reliable relay and settlement layer. It takes the AI-generated calldata and the user's EIP-712 signatures, wraps them into a transaction, and ensures they are successfully mined on-chain against our `SettlementRouter.sol`. This abstracts away the complexity of transaction broadcasting and allows our AI agents to focus purely on finding the best execution route.

## Security Model

| Layer | What it blocks | How |
|-------|---------------|-----|
| **Agent Guardrails** | AI Hallucinations | Agent only proposes executions; it cannot deduct balances. Strict formatting ensures fake calldata is rejected. |
| **EIP-712 Signatures** | Unauthorized execution | Funds in IntentVault only move if SettlementRouter gets a signature matching the swap parameters. |
| **Price Limits** | Slippage & MEV | The smart contract strictly enforces minAmountOut, blocking any execution that results in high slippage. |

## Deployed Contracts (Sepolia Testnet)

| Contract | Address |
|----------|---------|
| IntentVault | `0xF1Defe986257b2e8A74f40A48dbe3673268709f4` |
| SettlementRouter | `0x98d83435F4aBcE9AdC2C1635125e5f627b7d73E0` |
| PriorityFee | `0xbF57d7f6d829A647F880BBE18bbEF8e66DC15C61` |
| ProtocolTreasury | `0x20E8fcF701F6C2CDd74263Fa43989a80c9627c6C` |
| DarkPoolOTC | `0x6Db1f514752909aFA636DBc05B8541C8bcf117b5` |

## Project Structure

```text
CactusNetwork/
├── FEEDBACK.md   # Hackathon DX Feedback & KeeperHub Bug Reports (Required)
├── foundry/      # Smart Contracts (IntentVault, SettlementRouter, PriorityFee, ProtocolTreasury, DarkPoolOTC)
├── backend/      # Python FastAPI (Orchestrator, Grok Agent, DBs)
└── frontend/     # Next.js (Web3 Login, Order Intent UI)
```

## Getting Started

### Prerequisites
- Node.js 18+ (for frontend)
- Python 3.10+ (for backend)

### Backend Setup
```bash
cd backend
pip install -r requirements.txt
# Create a .env file with your Uniswap API Key and Grok API Key
python -m uvicorn scripts.api_server:app --reload --port 8000
```

### Frontend Setup
```bash
cd frontend
pnpm install
pnpm run dev     # starts on http://localhost:3000
```

## Team Members

- **Shi Jui Line**: Smart Contract Development
  - Designed and implemented the smart contract
  - Developed contract testing and deployment scripts
  - Design and deploy the KeeperHub workflow
  - Email: ark009770@gmail.com

- **ansenchen**: Backend Development
  - Orchestrator & Agent Workflows
  - Integrated Uniswap V3 Trading API for real-time on-chain quotes and KeeperHub as the automated transaction relay layer.
  - Database Design
  - Email: ansenchen.12@gmail.com

- **Chun Han Wu**: Project Assistant
  - Created project presentations and demo videos
  - Handled administrative tasks
  - Email: ansenchen.12@gmail.com

- **Yu I Lin**: Project Assistant
  - Developed a responsive and intuitive frontend interface using Next.js/React, focusing on seamless user experience (UX).
  - Architected the communication layer to integrate backend APIs, ensuring efficient data fetching and real-time state management.
  - Visual Design
  - Email: t112AB0004@ntut.org.tw

## Demo Video

## License
MIT
