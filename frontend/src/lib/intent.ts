import type { WalletClient } from 'viem';
import type { IntentJson } from '@/types/api';

const SEPOLIA_CHAIN_ID = 11155111;
const USDC_SEPOLIA = '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238';
const WETH_SEPOLIA = '0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14';
const SETTLEMENT_ROUTER_SEPOLIA = '0x98d83435F4aBcE9AdC2C1635125e5f627b7d73E0';
const INTENT_VAULT_ADDRESS = '0xF1Defe986257b2e8A74f40A48dbe3673268709f4';
const WETH_DECIMALS = 18;
const USDC_DECIMALS = 6;
const DEFAULT_UNISWAP_V3_FEE = 100;
const DEFAULT_PRICE_LIMIT = 0;

type OrderSide = 'buy' | 'sell';

interface BuildSignedIntentInput {
  side: OrderSide;
  user: `0x${string}`;
  walletClient?: WalletClient;
  amount: number;
  unitPriceUsdc: number;
}

interface EthereumProvider {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
}

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

const intentTypes = {
  UserIntent: [
    { name: 'user', type: 'address' },
    { name: 'tokenIn', type: 'address' },
    { name: 'tokenOut', type: 'address' },
    { name: 'amountIn', type: 'uint256' },
    { name: 'minAmountOut', type: 'uint256' },
    { name: 'deadline', type: 'uint256' },
    { name: 'salt', type: 'bytes32' },
    { name: 'allowPartialFill', type: 'bool' },
  ],
} as const;

// ERC20 approve ABI
const ERC20_ABI = [
  {
    name: 'approve',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'amount', type: 'uint256' },
    ],
    outputs: [{ name: '', type: 'bool' }],
  },
] as const;

// IntentVault deposit ABI
const INTENT_VAULT_ABI = [
  {
    name: 'deposit',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'token', type: 'address' },
      { name: 'amount', type: 'uint256' },
    ],
    outputs: [],
  },
  {
    name: 'depositETH',
    type: 'function',
    stateMutability: 'payable',
    inputs: [],
    outputs: [],
  },
] as const;

// 建立前端送給後端的鏈上 intent，並請錢包用 EIP-712 簽名。
export async function buildSignedIntent({
  side,
  user,
  walletClient,
  amount,
  unitPriceUsdc,
}: BuildSignedIntentInput): Promise<{ intent_json: IntentJson; signature: string }> {
  const amountIn = side === 'sell'
    ? toTokenUnits(amount, WETH_DECIMALS)
    : toTokenUnits(amount * unitPriceUsdc, USDC_DECIMALS);
  const minAmountOut = side === 'sell'
    ? toTokenUnits(amount * unitPriceUsdc, USDC_DECIMALS)
    : toTokenUnits(amount, WETH_DECIMALS);
  const intent = {
    user,
    tokenIn: side === 'sell' ? WETH_SEPOLIA : USDC_SEPOLIA,
    tokenOut: side === 'sell' ? USDC_SEPOLIA : WETH_SEPOLIA,
    amountIn: amountIn.toString(),
    minAmountOut: minAmountOut.toString(),
    deadline: Math.floor(Date.now() / 1000) + 60 * 30,
    salt: randomBytes32(),
    allowPartialFill: true,
  };

  const typedData = {
    domain: {
      name: 'SettlementRouter',
      version: '1',
      chainId: SEPOLIA_CHAIN_ID,
      verifyingContract: SETTLEMENT_ROUTER_SEPOLIA,
    },
    types: intentTypes,
    primaryType: 'UserIntent' as const,
    message: {
      user: intent.user,
      tokenIn: intent.tokenIn,
      tokenOut: intent.tokenOut,
      amountIn,
      minAmountOut,
      deadline: BigInt(intent.deadline),
      salt: intent.salt as `0x${string}`,
      allowPartialFill: intent.allowPartialFill,
    },
  };
  const signature = walletClient
    ? await walletClient.signTypedData({
        account: user,
        ...typedData,
      })
    : await signTypedDataWithInjectedWallet(user, typedData);

  return {
    intent_json: {
      ...intent,
      chainId: SEPOLIA_CHAIN_ID,
      tokenInChainId: SEPOLIA_CHAIN_ID,
      tokenOutChainId: SEPOLIA_CHAIN_ID,
      fee: DEFAULT_UNISWAP_V3_FEE,
      priceLimit: DEFAULT_PRICE_LIMIT,
      swapper: user,
      recipient: user,
    },
    signature: normalizeHexSignature(signature),
  };
}

/**
 * 授權 token 並存入 IntentVault。
 * 賣單 (tokenIn=WETH)：可選 depositETH (直接 ETH) 或 deposit (已有 WETH)
 * 買單 (tokenIn=USDC)：approve → deposit
 *
 * @param useETH 若 true 且 side=sell，使用 depositETH 直接存入 ETH
 */
export async function depositToVault({
  walletClient,
  publicClient,
  user,
  side,
  amount,
  unitPriceUsdc,
  useETH = true,
}: {
  walletClient: WalletClient;
  publicClient: any; // Use wagmi PublicClient
  user: `0x${string}`;
  side: OrderSide;
  amount: number;
  unitPriceUsdc: number;
  useETH?: boolean;
}): Promise<void> {
  const amountIn = side === 'sell'
    ? toTokenUnits(amount, WETH_DECIMALS)
    : toTokenUnits(amount * unitPriceUsdc, USDC_DECIMALS);
  const tokenIn = side === 'sell' ? WETH_SEPOLIA : USDC_SEPOLIA;

  if (side === 'sell' && useETH) {
    // 直接用 ETH 存入，合約會自動 wrap 為 WETH
    const hash = await walletClient.writeContract({
      address: INTENT_VAULT_ADDRESS as `0x${string}`,
      abi: INTENT_VAULT_ABI,
      functionName: 'depositETH',
      args: [],
      value: amountIn,
      account: user,
      chain: undefined, // Let it infer chain
    });
    await publicClient.waitForTransactionReceipt({ hash });
  } else {
    // ERC20: approve → deposit
    const approveHash = await walletClient.writeContract({
      address: tokenIn as `0x${string}`,
      abi: ERC20_ABI,
      functionName: 'approve',
      args: [INTENT_VAULT_ADDRESS as `0x${string}`, amountIn],
      account: user,
      chain: undefined,
    });
    await publicClient.waitForTransactionReceipt({ hash: approveHash });

    const depositHash = await walletClient.writeContract({
      address: INTENT_VAULT_ADDRESS as `0x${string}`,
      abi: INTENT_VAULT_ABI,
      functionName: 'deposit',
      args: [tokenIn as `0x${string}`, amountIn],
      account: user,
      chain: undefined,
    });
    await publicClient.waitForTransactionReceipt({ hash: depositHash });
  }
}

// Contract addresses export for reuse
export const CONTRACTS = {
  INTENT_VAULT: INTENT_VAULT_ADDRESS,
  SETTLEMENT_ROUTER: SETTLEMENT_ROUTER_SEPOLIA,
  WETH: WETH_SEPOLIA,
  USDC: USDC_SEPOLIA,
  CHAIN_ID: SEPOLIA_CHAIN_ID,
} as const;

// 確保送進後端的 signature 永遠符合 0x-prefixed hex 格式。
function normalizeHexSignature(signature: string): string {
  return signature.startsWith('0x') ? signature : `0x${signature}`;
}

// 測試 provider 或部分瀏覽器環境可能還拿不到 wagmi walletClient，此時直接走 MetaMask JSON-RPC。
async function signTypedDataWithInjectedWallet(
  user: `0x${string}`,
  typedData: {
    domain: { name: string; version: string; chainId: number; verifyingContract: string };
    types: typeof intentTypes;
    primaryType: 'UserIntent';
    message: {
      user: string;
      tokenIn: string;
      tokenOut: string;
      amountIn: bigint;
      minAmountOut: bigint;
      deadline: bigint;
      salt: `0x${string}`;
      allowPartialFill: boolean;
    };
  },
): Promise<string> {
  if (!window.ethereum) {
    throw new Error('找不到錢包 provider');
  }
  const jsonSafeTypedData = {
    ...typedData,
    message: {
      ...typedData.message,
      amountIn: typedData.message.amountIn.toString(),
      minAmountOut: typedData.message.minAmountOut.toString(),
      deadline: typedData.message.deadline.toString(),
    },
  };
  const signature = await window.ethereum.request({
    method: 'eth_signTypedData_v4',
    params: [user, JSON.stringify(jsonSafeTypedData)],
  });
  if (typeof signature !== 'string') {
    throw new Error('錢包簽名回傳格式錯誤');
  }
  return signature;
}

// 將人類輸入的小數數量轉成 ERC20 raw unit 字串使用的 bigint。
function toTokenUnits(value: number, decimals: number): bigint {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error('數量必須大於 0');
  }
  const fixed = value.toFixed(decimals);
  const [whole, fraction = ''] = fixed.split('.');
  return BigInt(whole + fraction.padEnd(decimals, '0').slice(0, decimals));
}

// 產生 EIP-712 intent 使用的 bytes32 salt。
function randomBytes32(): `0x${string}` {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return `0x${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}
