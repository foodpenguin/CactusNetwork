import type { WalletClient, PublicClient } from 'viem';
import { CONTRACTS } from './intent';

const PRIORITY_FEE_ADDRESS = '0xbF57d7f6d829A647F880BBE18bbEF8e66DC15C61';
const USDC_DECIMALS = 6;

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
  {
    name: 'allowance',
    type: 'function',
    stateMutability: 'view',
    inputs: [
      { name: 'owner', type: 'address' },
      { name: 'spender', type: 'address' },
    ],
    outputs: [{ name: '', type: 'uint256' }],
  },
] as const;

const PRIORITY_FEE_ABI = [
  {
    name: 'pay',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: '_token', type: 'address' },
      { name: '_amount', type: 'uint256' },
    ],
    outputs: [],
  },
] as const;

export const PLAN_AMOUNTS: Record<string, number> = {
  plus: 20,
  max: 60,
};

/**
 * 檢查 USDC 授權額度。
 */
export async function checkAllowance({
  publicClient,
  user,
}: {
  publicClient: PublicClient;
  user: `0x${string}`;
}): Promise<bigint> {
  return await publicClient.readContract({
    address: CONTRACTS.USDC as `0x${string}`,
    abi: ERC20_ABI,
    functionName: 'allowance',
    args: [user, PRIORITY_FEE_ADDRESS as `0x${string}`],
  });
}

/**
 * 授權 USDC 給 PriorityFee 合約。
 */
export async function approvePriorityFee({
  walletClient,
  user,
  amountUsdc,
}: {
  walletClient: WalletClient;
  user: `0x${string}`;
  amountUsdc: number;
}): Promise<`0x${string}`> {
  const rawAmount = BigInt(amountUsdc) * BigInt(10 ** USDC_DECIMALS);
  const hash = await walletClient.writeContract({
    address: CONTRACTS.USDC as `0x${string}`,
    abi: ERC20_ABI,
    functionName: 'approve',
    args: [PRIORITY_FEE_ADDRESS as `0x${string}`, rawAmount],
    account: user,
    chain: undefined,
  });
  return hash;
}

/**
 * 呼叫 PriorityFee.pay() 付款。
 */
export async function payPriorityFee({
  walletClient,
  user,
  amountUsdc,
}: {
  walletClient: WalletClient;
  user: `0x${string}`;
  amountUsdc: number;
}): Promise<`0x${string}`> {
  const rawAmount = BigInt(amountUsdc) * BigInt(10 ** USDC_DECIMALS);
  const hash = await walletClient.writeContract({
    address: PRIORITY_FEE_ADDRESS as `0x${string}`,
    abi: PRIORITY_FEE_ABI,
    functionName: 'pay',
    args: [CONTRACTS.USDC as `0x${string}`, rawAmount],
    account: user,
    chain: undefined,
  });
  return hash;
}
