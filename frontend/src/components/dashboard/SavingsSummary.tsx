'use client';

import { useLanguage } from '@/contexts/LanguageContext';

interface Props {
  savedUsdc: number;
  makerProfit: number;
  totalOrders: number;
}

export function SavingsSummary({ savedUsdc, makerProfit, totalOrders }: Props) {
  const { t } = useLanguage();
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div
        className="rounded-2xl p-5"
        style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      >
        <div className="text-xs font-medium mb-1" style={{ color: '#888' }}>{t.summary.savedLabel}</div>
        <div className="text-2xl font-bold" style={{ color: '#4caf7d' }}>
          ${savedUsdc.toLocaleString()} <span className="text-sm font-normal">USDC</span>
        </div>
      </div>
      <div
        className="rounded-2xl p-5"
        style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      >
        <div className="text-xs font-medium mb-1" style={{ color: '#888' }}>{t.summary.makerLabel}</div>
        <div className="text-2xl font-bold" style={{ color: '#f2a8b4' }}>
          ${makerProfit.toFixed(2)} <span className="text-sm font-normal">USDC</span>
        </div>
      </div>
      <div
        className="rounded-2xl p-5"
        style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      >
        <div className="text-xs font-medium mb-1" style={{ color: '#888' }}>{t.summary.ordersLabel}</div>
        <div className="text-2xl font-bold" style={{ color: '#1c1c1c' }}>{totalOrders}</div>
      </div>
    </div>
  );
}
