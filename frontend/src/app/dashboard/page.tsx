'use client';

import { ActiveOrdersTable } from '@/components/dashboard/ActiveOrdersTable';
import { PriceChart } from '@/components/dashboard/PriceChart';
import { useLanguage } from '@/contexts/LanguageContext';
import { useBuyOrders, useSellOrders } from '@/hooks/useOrders';
import { useAuth } from '@/hooks/useAuth';
import type { AnyOrder, OrderStatus } from '@/types/api';

export default function DashboardPage() {
  const { t } = useLanguage();
  const { isAuthenticated } = useAuth();
  const { data: buyOrders = [] } = useBuyOrders();
  const { data: sellOrders = [] } = useSellOrders();

  // 合併買賣單為 ActiveOrdersTable 可用格式
  const allOrders: AnyOrder[] = [
    ...buyOrders.map((o) => ({
      ...o,
      direction: 'BUY' as const,
      orderId: o.buyOrderId,
    })),
    ...sellOrders.map((o) => ({
      ...o,
      direction: 'SELL' as const,
      orderId: o.sellOrderId,
    })),
  ].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());

  const tableRows = allOrders.map((o) => ({
    orderId: o.orderId,
    direction: o.direction,
    asset: o.asset,
    amount: o.amount,
    status: o.status as OrderStatus,
    createdAt: o.createdAt,
  }));

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: '#1c1c1c' }}>{t.dashboard.title}</h1>
        <p className="text-sm mt-1" style={{ color: '#888' }}>{t.dashboard.desc}</p>
      </div>

      {!isAuthenticated && (
        <div className="rounded-xl p-4 text-sm text-center" style={{ background: '#fef3c7', color: '#92400e' }}>
          {t.orderForm.connectFirst}
        </div>
      )}

      <PriceChart data={[]} basePrice={0} />

      <ActiveOrdersTable orders={tableRows} />
    </div>
  );
}
