'use client';

import { useState } from 'react';
import { OrderForm } from '@/components/trade/OrderForm';
import { AgentTerminal } from '@/components/trade/AgentTerminal';
import { useAgentLog } from '@/hooks/useAgentLog';
import { useLanguage } from '@/contexts/LanguageContext';

export default function TradePage() {
  const { t } = useLanguage();
  const { logs, running, start } = useAgentLog();
  const [progress, setProgress] = useState({ current: 0, total: 0, savedUsdc: 0, makerProfit: 0 });

  function handleOrderSubmitted(slippage: number, splits: number, chunkSize: number) {
    start(slippage, splits, chunkSize);
    const savedUsdc = Math.round(slippage * chunkSize * 3000 * splits * 0.01);
    const makerProfit = chunkSize * 3000 * 0.003 * splits;
    setProgress({ current: 1, total: splits, savedUsdc, makerProfit });
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold" style={{ color: '#1c1c1c' }}>{t.trade.title}</h1>
        <p className="text-sm mt-1" style={{ color: '#888' }}>
          {t.trade.desc}
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
        {/* Order Form — 2/5 */}
        <div className="lg:col-span-2">
          <OrderForm onOrderSubmitted={handleOrderSubmitted} />
        </div>

        {/* Agent Terminal — 3/5 */}
        <div className="lg:col-span-3">
          <AgentTerminal logs={logs} running={running} progress={progress} />
        </div>
      </div>
    </div>
  );
}
