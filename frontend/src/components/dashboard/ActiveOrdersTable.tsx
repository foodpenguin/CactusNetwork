'use client';

import type { OrderStatus } from '@/types/api';
import { useLanguage } from '@/contexts/LanguageContext';

interface OrderRow {
  orderId: number;
  direction: 'BUY' | 'SELL';
  asset: string;
  amount: number;
  status: OrderStatus;
  createdAt: string;
  txHash?: string;
}

interface Props {
  orders: OrderRow[];
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  pending: { bg: '#fef3c7', color: '#92400e' },
  confirmed: { bg: '#dcfce7', color: '#166534' },
  filled: { bg: '#dcfce7', color: '#166534' },
  failed: { bg: '#fee2e2', color: '#991b1b' },
  invalid: { bg: '#fee2e2', color: '#991b1b' },
  timeout: { bg: '#fef3c7', color: '#92400e' },
};

export function ActiveOrdersTable({ orders }: Props) {
  const { t } = useLanguage();

  const cols = ['Order ID', t.ordersTable.colDirection, t.ordersTable.colAsset, t.ordersTable.colAmount, t.ordersTable.colStatus, t.ordersTable.colTime, 'Tx'];

  const statusLabel: Record<string, string> = {
    pending: t.ordersTable.statusPending,
    confirmed: t.ordersTable.statusConfirmed,
    filled: t.ordersTable.statusConfirmed,
    failed: t.ordersTable.statusFailed,
    invalid: t.ordersTable.statusFailed,
    timeout: 'Timeout',
  };

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
    >
      <div className="px-5 py-4 border-b font-semibold text-sm" style={{ borderColor: '#e8ddd5', color: '#1c1c1c' }}>
        {t.ordersTable.title}
      </div>
      {orders.length === 0 ? (
        <div className="px-5 py-10 text-center text-sm" style={{ color: '#aaa' }}>
          {t.ordersTable.empty}
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: '#faf5f0' }}>
              {cols.map((h) => (
                <th
                  key={h}
                  className="text-left px-5 py-3 font-medium text-xs"
                  style={{ color: '#888' }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => {
              const colors = STATUS_COLORS[o.status] || { bg: '#f3f4f6', color: '#374151' };
              return (
                <tr key={o.orderId} className="border-t" style={{ borderColor: '#f5f0eb' }}>
                  <td className="px-5 py-3 font-mono text-xs" style={{ color: '#888' }}>#{o.orderId}</td>
                  <td className="px-5 py-3">
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-semibold"
                      style={{
                        background: o.direction === 'SELL' ? '#fde8ec' : '#dcfce7',
                        color: o.direction === 'SELL' ? '#e07585' : '#166534',
                      }}
                    >
                      {o.direction}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-medium">{o.asset}</td>
                  <td className="px-5 py-3">{o.amount}</td>
                  <td className="px-5 py-3">
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-semibold"
                      style={{ background: colors.bg, color: colors.color }}
                    >
                      {statusLabel[o.status] || o.status}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-xs" style={{ color: '#aaa' }}>
                    {new Date(o.createdAt).toLocaleString(t.ordersTable.localeTime)}
                  </td>
                  <td className="px-5 py-3 text-xs">
                    {o.txHash ? (
                      <a
                        href={`https://sepolia.etherscan.io/tx/${o.txHash}`}
                        target="_blank"
                        rel="noreferrer"
                        className="underline hover:opacity-80 transition-opacity"
                        style={{ color: '#e07585' }}
                      >
                        Etherscan
                      </a>
                    ) : (
                      <span style={{ color: '#ccc' }}>-</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
