// ── Auth ──────────────────────────────────────────────

export interface AccountResponse {
  message: string;
  accountName: string;
  publicKey: string;
  accountLevel: string;
  day: number;
  createdAt: string;
}

export interface LoginResponse {
  message: string;
  tokenType: 'Bearer';
  accessToken: string;
  expiresAt: string;
}

// ── Orders ────────────────────────────────────────────

export interface IntentJson {
  user: string;
  tokenIn: string;
  tokenOut: string;
  amountIn: string;
  minAmountOut: string;
  deadline: number;
  salt: string;
  allowPartialFill: boolean;
}

export interface BuyOrderRequest {
  asset: string;
  amount: number;
  max_unit_price_usdc: number;
  max_splits: number;
  max_fee_percent: number;
  intent_json?: IntentJson;
  signature?: string;
}

export interface SellOrderRequest {
  asset: string;
  amount: number;
  min_unit_price_usdc: number;
  max_splits: number;
  max_fee_percent: number;
  intent_json?: IntentJson;
  signature?: string;
}

export type OrderStatus = 'pending' | 'confirmed' | 'failed';

export interface BuyOrderResponse {
  message: string;
  buyOrderId: number;
  accountName: string;
  accountLevelSnapshot: string;
  asset: string;
  amount: number;
  remainingAmount: number;
  maxUnitPriceUsdc: number;
  maxSplits: number;
  maxFeePercent: number;
  status: OrderStatus;
  attempts: number;
  hasIntent: boolean;
  hasSignature: boolean;
  createdAt: string;
}

export interface SellOrderResponse {
  message: string;
  sellOrderId: number;
  accountName: string;
  accountLevelSnapshot: string;
  asset: string;
  amount: number;
  remainingAmount: number;
  minUnitPriceUsdc: number;
  maxSplits: number;
  maxFeePercent: number;
  status: OrderStatus;
  attempts: number;
  hasIntent: boolean;
  hasSignature: boolean;
  createdAt: string;
  queueAt: string;
}

export type AnyOrder = (BuyOrderResponse | SellOrderResponse) & {
  direction: 'BUY' | 'SELL';
  orderId: number;
};
