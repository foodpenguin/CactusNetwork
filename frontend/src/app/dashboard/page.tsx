'use client';

import { SavingsSummary } from '@/components/dashboard/SavingsSummary';
import { ActiveOrdersTable } from '@/components/dashboard/ActiveOrdersTable';
import { PriceChart } from '@/components/dashboard/PriceChart';
import { useLanguage } from '@/contexts/LanguageContext';

// 模擬資料（後端串接後替換）
const MOCK_ORDERS = [
  { orderId: 1, direction: 'SELL' as const, asset: 'WETH', amount: 1000, status: 'confirmed' as const, createdAt: '2026-05-01T12:00:00+00:00' },
  { orderId: 2, direction: 'BUY' as const, asset: 'WETH', amount: 50, status: 'pending' as const, createdAt: '2026-05-01T12:30:00+00:00' },
  { orderId: 3, direction: 'SELL' as const, asset: 'WETH', amount: 200, status: 'failed' as const, createdAt: '2026-05-01T13:00:00+00:00' },
];

// 模擬子單成交價（幾乎持平，展示零滑價）
const MOCK_PRICE_DATA = Array.from({ length: 100 }, (_, i) => ({
  n: i + 1,
  price: 3000 + (Math.random() - 0.5) * 0.6,
}));

export default function DashboardPage() {
  const { t } = useLanguage();
  return (
    <div className="max-w-7xl mx-auto px-4 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: '#1c1c1c' }}>{t.dashboard.title}</h1>
        <p className="text-sm mt-1" style={{ color: '#888' }}>{t.dashboard.desc}</p>
      </div>

      <SavingsSummary savedUsdc={1500} makerProfit={9.27} totalOrders={3} />

      <PriceChart data={MOCK_PRICE_DATA} basePrice={3000} />

      <ActiveOrdersTable orders={MOCK_ORDERS} />
    </div>
  );
}
