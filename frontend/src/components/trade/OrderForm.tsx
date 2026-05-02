'use client';

import { useState } from 'react';
import { useBuyOrder, useSellOrder } from '@/hooks/useOrders';
import { useAuth } from '@/hooks/useAuth';
import { useLanguage } from '@/contexts/LanguageContext';
import { useWalletClient, usePublicClient } from 'wagmi';
import { buildSignedIntent, depositToVault } from '@/lib/intent';

interface Props {
  onOrderSubmitted: (orderId: number, direction: 'BUY' | 'SELL') => void;
}

const ASSETS = ['WETH'];

export function OrderForm({ onOrderSubmitted }: Props) {
  const { address, isAuthenticated } = useAuth();
  const { t } = useLanguage();
  const { data: walletClient } = useWalletClient();
  const publicClient = usePublicClient();
  const [tab, setTab] = useState<'buy' | 'sell'>('sell');
  const [asset] = useState('WETH');
  const [amount, setAmount] = useState('');
  const [unitPrice, setUnitPrice] = useState('');
  const [maxSplits, setMaxSplits] = useState('10');
  const [maxFee, setMaxFee] = useState('0.3');
  const [submitError, setSubmitError] = useState('');
  const [step, setStep] = useState<'idle' | 'signing' | 'depositing' | 'submitting'>('idle');

  const buyMutation = useBuyOrder();
  const sellMutation = useSellOrder();
  const isPending = step !== 'idle';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isAuthenticated || !address || !walletClient) return;
    const amt = parseFloat(amount);
    const price = parseFloat(unitPrice);
    const splits = parseInt(maxSplits);
    const fee = parseFloat(maxFee);
    if (!amt || !price) return;

    try {
      setSubmitError('');

      // Step 1: 簽署 EIP-712
      setStep('signing');
      const signedIntent = await buildSignedIntent({
        side: tab,
        user: address,
        walletClient,
        amount: amt,
        unitPriceUsdc: price,
      });

      // Step 2: 授權 + 存入 IntentVault
      setStep('depositing');
      await depositToVault({
        walletClient,
        publicClient,
        user: address,
        side: tab,
        amount: amt,
        unitPriceUsdc: price,
        useETH: tab === 'sell',
      });

      // Step 3: 呼叫後端 API 建立訂單
      setStep('submitting');
      if (tab === 'sell') {
        const res = await sellMutation.mutateAsync({
          asset,
          amount: amt,
          min_unit_price_usdc: price,
          max_splits: splits,
          max_fee_percent: fee,
          ...signedIntent,
        });
        onOrderSubmitted(res.sellOrderId, 'SELL');
      } else {
        const res = await buyMutation.mutateAsync({
          asset,
          amount: amt,
          max_unit_price_usdc: price,
          max_splits: splits,
          max_fee_percent: fee,
          ...signedIntent,
        });
        onOrderSubmitted(res.buyOrderId, 'BUY');
      }
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : '送出訂單失敗');
    } finally {
      setStep('idle');
    }
  }

  const stepLabels: Record<string, string> = {
    signing: t.orderForm.stepSigning ?? '簽署中...',
    depositing: t.orderForm.stepDepositing ?? '存入 Vault 中...',
    submitting: t.orderForm.submitting,
  };

  const error = submitError || buyMutation.error?.message || sellMutation.error?.message;

  return (
    <div
      className="rounded-2xl p-6 flex flex-col gap-4"
      style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
    >
      <h2 className="text-lg font-bold" style={{ color: '#1c1c1c' }}>{t.orderForm.title}</h2>

      {/* Tab */}
      <div
        className="flex rounded-xl p-1 gap-1"
        style={{ background: '#faf5f0' }}
      >
        {(['sell', 'buy'] as const).map((tab_) => (
          <button
            key={tab_}
            onClick={() => setTab(tab_)}
            className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all"
            style={{
              background: tab === tab_ ? (tab_ === 'sell' ? '#f2a8b4' : '#86efac') : 'transparent',
              color: '#1c1c1c',
            }}
          >
            {tab_ === 'sell' ? t.orderForm.tabSell : t.orderForm.tabBuy}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        {/* Asset */}
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: '#888' }}>{t.orderForm.labelAsset}</label>
          <div
            className="px-3 py-2.5 rounded-xl text-sm font-medium"
            style={{ background: '#faf5f0', color: '#1c1c1c', border: '1px solid #e8ddd5' }}
          >
            {ASSETS[0]}
          </div>
        </div>

        {/* Amount */}
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: '#888' }}>
            {t.orderForm.labelAmount}
          </label>
          <input
            type="number"
            min="0.001"
            step="any"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={t.orderForm.amountPlaceholder}
            required
            className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
            style={{ background: '#faf5f0', color: '#1c1c1c', border: '1px solid #e8ddd5' }}
          />
        </div>

        {/* Unit price */}
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: '#888' }}>
            {tab === 'sell' ? t.orderForm.labelMinPrice : t.orderForm.labelMaxPrice}
          </label>
          <input
            type="number"
            min="0"
            step="any"
            value={unitPrice}
            onChange={(e) => setUnitPrice(e.target.value)}
            placeholder={t.orderForm.pricePlaceholder}
            required
            className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
            style={{ background: '#faf5f0', color: '#1c1c1c', border: '1px solid #e8ddd5' }}
          />
        </div>

        {/* Max splits + fee */}
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="text-xs font-medium mb-1 block" style={{ color: '#888' }}>{t.orderForm.labelMaxSplits}</label>
            <input
              type="number"
              min="1"
              max="200"
              value={maxSplits}
              onChange={(e) => setMaxSplits(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: '#faf5f0', color: '#1c1c1c', border: '1px solid #e8ddd5' }}
            />
          </div>
          <div className="flex-1">
            <label className="text-xs font-medium mb-1 block" style={{ color: '#888' }}>{t.orderForm.labelMaxFee}</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={maxFee}
              onChange={(e) => setMaxFee(e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: '#faf5f0', color: '#1c1c1c', border: '1px solid #e8ddd5' }}
            />
          </div>
        </div>

        {error && (
          <div className="text-xs px-3 py-2 rounded-lg" style={{ background: '#fee2e2', color: '#991b1b' }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={!isAuthenticated || isPending}
          className="py-3 rounded-xl font-semibold text-sm transition-all hover:opacity-90 disabled:opacity-40"
          style={{ background: '#f2a8b4', color: '#1c1c1c' }}
        >
          {!isAuthenticated
            ? t.orderForm.connectFirst
            : isPending
            ? (stepLabels[step] || t.orderForm.submitting)
            : tab === 'sell' ? t.orderForm.submitSell : t.orderForm.submitBuy}
        </button>
      </form>
    </div>
  );
}
