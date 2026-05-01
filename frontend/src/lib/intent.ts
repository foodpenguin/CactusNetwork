import type { WalletClient } from 'viem';
import type { IntentJson } from '@/types/api';

const SEPOLIA_CHAIN_ID = 11155111;
const USDC_SEPOLIA = '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238';
const WETH_SEPOLIA = '0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14';
const SETTLEMENT_ROUTER_SEPOLIA = '0x98d83435F4aBcE9AdC2C1635125e5f627b7d73E0';
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
