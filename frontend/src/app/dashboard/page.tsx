'use client';

import { ActiveOrdersTable } from '@/components/dashboard/ActiveOrdersTable';
import { useLanguage } from '@/contexts/LanguageContext';
import { useBuyOrders, useSellOrders, useExecutions } from '@/hooks/useOrders';
import { useAuth } from '@/hooks/useAuth';
import type { AnyOrder, OrderStatus } from '@/types/api';

export default function DashboardPage() {
  const { t } = useLanguage();
  const { isAuthenticated } = useAuth();
  const { data: buyOrders = [] } = useBuyOrders();
  const { data: sellOrders = [] } = useSellOrders();
  const { data: executions = [] } = useExecutions();

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

  const getTxHash = (orderId: number, direction: 'BUY' | 'SELL') => {
    if (direction === 'SELL') {
      const exec = executions.find(e => e.sellOrderId === orderId);
      return exec ? exec.executionId : undefined;
    }
    // BUY orders currently don't map directly to executionId in the response model easily without an execution list specifically for buy_order, 
    // but if relatedBy is implemented in the frontend model, we can try to find it. Currently execution model only has sellOrderId.
    return undefined;
  };

  const tableRows = allOrders.map((o) => ({
    orderId: o.orderId,
    direction: o.direction,
    asset: o.asset,
    amount: o.amount,
    status: o.status as OrderStatus,
    createdAt: o.createdAt,
    txHash: getTxHash(o.orderId, o.direction),
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

      <ActiveOrdersTable orders={tableRows} />
    </div>
  );
}
