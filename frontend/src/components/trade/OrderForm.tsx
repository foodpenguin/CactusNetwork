'use client';

import { useState } from 'react';
import { useBuyOrder, useSellOrder } from '@/hooks/useOrders';
import { useAuth } from '@/hooks/useAuth';
import { SlippageWarning } from './SlippageWarning';
import { useLanguage } from '@/contexts/LanguageContext';
import { useWalletClient } from 'wagmi';
import { buildSignedIntent } from '@/lib/intent';

interface Props {
  onOrderSubmitted: (slippage: number, splits: number, chunkSize: number) => void;
}

const ASSETS = ['WETH'];

// 模擬滑價計算：amount 越大，滑價越高
function estimateSlippage(amount: number): number {
  if (amount <= 5) return 0.3;
  if (amount <= 20) return 1.2;
  if (amount <= 50) return 2.8;
  if (amount <= 100) return 4.8;
  return Math.min(amount * 0.048, 15);
}

export function OrderForm({ onOrderSubmitted }: Props) {
  const { address, isAuthenticated } = useAuth();
  const { t } = useLanguage();
  const { data: walletClient } = useWalletClient();
  const [tab, setTab] = useState<'buy' | 'sell'>('sell');
  const [asset] = useState('WETH');
  const [amount, setAmount] = useState('');
  const [unitPrice, setUnitPrice] = useState('');
  const [maxSplits, setMaxSplits] = useState('10');
  const [maxFee, setMaxFee] = useState('0.3');
  const [slippage, setSlippage] = useState(0);
  const [estimated, setEstimated] = useState(false);
  const [submitError, setSubmitError] = useState('');

  const buyMutation = useBuyOrder();
  const sellMutation = useSellOrder();
  const isPending = buyMutation.isPending || sellMutation.isPending;

  function handleEstimate() {
    const amt = parseFloat(amount);
    if (!amt || amt <= 0) return;
    const s = estimateSlippage(amt);
    setSlippage(s);
    setEstimated(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isAuthenticated) return;
    const amt = parseFloat(amount);
    const price = parseFloat(unitPrice);
    const splits = parseInt(maxSplits);
    const fee = parseFloat(maxFee);
    if (!amt || !price) return;

    const sl = estimated ? slippage : estimateSlippage(amt);
    setSlippage(sl);

    const chunkSize = Math.ceil(amt / splits);

    try {
      setSubmitError('');
      if (!address) return;
      const signedIntent = await buildSignedIntent({
        side: tab,
        user: address,
        walletClient,
        amount: amt,
        unitPriceUsdc: price,
      });
      if (tab === 'sell') {
        await sellMutation.mutateAsync({
          asset,
          amount: amt,
          min_unit_price_usdc: price,
          max_splits: splits,
          max_fee_percent: fee,
          ...signedIntent,
        });
      } else {
        await buyMutation.mutateAsync({
          asset,
          amount: amt,
          max_unit_price_usdc: price,
          max_splits: splits,
          max_fee_percent: fee,
          ...signedIntent,
        });
      }
      onOrderSubmitted(sl, splits, chunkSize);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : '送出訂單失敗');
    }
  }

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
            onChange={(e) => { setAmount(e.target.value); setEstimated(false); setSlippage(0); }}
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

        {/* Estimate slippage */}
        <button
          type="button"
          onClick={handleEstimate}
          disabled={!amount}
          className="text-sm py-2 rounded-xl font-medium transition-all hover:opacity-80"
          style={{ background: '#fde8ec', color: '#e07585', border: '1px solid #f2a8b4' }}
        >
          {t.orderForm.estimateSlippage}
        </button>

        {/* Slippage warning */}
        {slippage > 0 && <SlippageWarning slippage={slippage} />}

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
            ? t.orderForm.submitting
            : tab === 'sell' ? t.orderForm.submitSell : t.orderForm.submitBuy}
        </button>
      </form>
    </div>
  );
}
