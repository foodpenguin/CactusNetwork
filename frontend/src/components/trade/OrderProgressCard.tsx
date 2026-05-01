'use client';

import { useLanguage } from '@/contexts/LanguageContext';

interface Props {
  current: number;
  total: number;
  savedUsdc: number;
  makerProfit: number;
}

export function OrderProgressCard({ current, total, savedUsdc, makerProfit }: Props) {
  const { t } = useLanguage();
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div
      className="rounded-xl p-4 mt-4"
      style={{ background: '#1f1f1f', border: '1px solid #333' }}
    >
      <div className="flex justify-between items-center mb-2">
        <span className="text-sm font-mono" style={{ color: '#f2a8b4' }}>
          {t.progressCard.chunkDone(current, total)}
        </span>
        <span className="text-xs font-mono" style={{ color: '#888' }}>{pct}%</span>
      </div>

      {/* Progress bar */}
      <div className="w-full rounded-full h-1.5 mb-3" style={{ background: '#333' }}>
        <div
          className="h-1.5 rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: '#f2a8b4' }}
        />
      </div>

      <div className="flex justify-between text-xs font-mono" style={{ color: '#aaa' }}>
        <span>{t.progressCard.saved} <span style={{ color: '#4caf7d' }}>${savedUsdc.toLocaleString()} USDC</span></span>
        <span>{t.progressCard.makerProfit} <span style={{ color: '#f2a8b4' }}>${makerProfit.toFixed(2)} USDC</span></span>
      </div>
    </div>
  );
}
