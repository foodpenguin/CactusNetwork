'use client';

import { useEffect, useState } from 'react';
import { ActiveOrdersTable } from '@/components/dashboard/ActiveOrdersTable';
import { useLanguage } from '@/contexts/LanguageContext';
import { useBuyOrders, useSellOrders, useExecutions } from '@/hooks/useOrders';
import { useAuth } from '@/hooks/useAuth';
import type { AnyOrder, OrderStatus } from '@/types/api';
import { usePublicClient } from 'wagmi';
import { parseAbiItem } from 'viem';
import { CONTRACTS } from '@/lib/intent';

export default function DashboardPage() {
  const { t } = useLanguage();
  const { address, isAuthenticated } = useAuth();
  const { data: buyOrders = [] } = useBuyOrders();
  const { data: sellOrders = [] } = useSellOrders();
  const { data: executions = [] } = useExecutions();
  const publicClient = usePublicClient();
  const [onChainTxs, setOnChainTxs] = useState<Record<string, string>>({}); // nonce -> txHash

  // 嘗試從區塊鏈撈取 OrderExecuted event，來解決後端丟失 txHash 的問題
  useEffect(() => {
    async function fetchEvents() {
      if (!publicClient || !address) return;
      try {
        const logs = await publicClient.getLogs({
          address: CONTRACTS.SETTLEMENT_ROUTER as `0x${string}`,
          event: parseAbiItem('event OrderExecuted(address indexed user, uint256 indexed nonce)'),
          args: { user: address as `0x${string}` },
          fromBlock: 'earliest',
        });
        
        const txMap: Record<string, string> = {};
        logs.forEach(log => {
          if (log.args.nonce && log.transactionHash) {
            txMap[log.args.nonce.toString()] = log.transactionHash;
          }
        });
        setOnChainTxs(txMap);
      } catch (err) {
        console.error('Failed to fetch on-chain events:', err);
      }
    }
    fetchEvents();
  }, [publicClient, address]);

  // 合併買賣單為 ActiveOrdersTable 可用格式
  const allOrders = [
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

  const getTxHash = (orderId: number, direction: 'BUY' | 'SELL', nonce: number) => {
    // 優先使用鏈上撈回來的 event txHash (以 nonce 比對)
    if (nonce && onChainTxs[nonce.toString()]) {
      return onChainTxs[nonce.toString()];
    }

    if (direction === 'SELL') {
      const exec = executions.find(e => e.sellOrderId === orderId);
      // 如果 DB 裡面的 executionId 看起來像 tx hash 也回傳
      if (exec?.executionId && exec.executionId.startsWith('0x')) {
          return exec.executionId;
      }
    }
    return undefined;
  };

  const tableRows = allOrders.map((o) => ({
    orderId: o.orderId,
    direction: o.direction,
    asset: o.asset,
    amount: o.amount,
    status: o.status as OrderStatus,
    createdAt: o.createdAt,
    txHash: getTxHash(o.orderId, o.direction, o.nonce),
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
