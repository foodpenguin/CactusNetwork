'use client';

import { useLanguage } from '@/contexts/LanguageContext';

interface Props {
  slippage: number;
}

export function SlippageWarning({ slippage }: Props) {
  const { t } = useLanguage();
  if (slippage <= 0) return null;

  const isHigh = slippage > 3;
  const bg = isHigh ? '#fee2e2' : '#fef3c7';
  const border = isHigh ? '#fca5a5' : '#fcd34d';
  const text = isHigh ? '#991b1b' : '#92400e';

  return (
    <div
      className="rounded-xl px-4 py-3 text-sm border"
      style={{ background: bg, borderColor: border, color: text }}
    >
      <div className="font-semibold mb-1">
        {isHigh ? t.slippage.highRisk : t.slippage.warning} {t.slippage.slippageMsg(slippage.toFixed(1))}
      </div>
      <div>
        {t.slippage.protection}
      </div>
    </div>
  );
}
