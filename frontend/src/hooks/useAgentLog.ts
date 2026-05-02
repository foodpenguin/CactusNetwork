'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { BuyOrderResponse, SellOrderResponse, ExecutionResponse } from '@/types/api';
import type { LogLine } from '@/components/trade/AgentTerminal';

function timestamp() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false });
}

export function useAgentLog() {
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [orderId, setOrderId] = useState<number | null>(null);
  const [direction, setDirection] = useState<'BUY' | 'SELL' | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevStatusRef = useRef<string>('');

  const addLog = useCallback((text: string) => {
    setLogs((prev) => [...prev, { timestamp: timestamp(), text }]);
  }, []);

  const start = useCallback((id: number, dir: 'BUY' | 'SELL') => {
    setLogs([]);
    setRunning(true);
    setOrderId(id);
    setDirection(dir);
    prevStatusRef.current = '';
  }, []);

  const clear = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    setLogs([]);
    setRunning(false);
    setOrderId(null);
    setDirection(null);
  }, []);

  useEffect(() => {
    if (!running || orderId === null || direction === null) return;

    addLog(`[System] Order #${orderId} (${direction}) submitted successfully. Monitoring status...`);

    const poll = async () => {
      try {
        if (direction === 'BUY') {
          const orders = await api.get<BuyOrderResponse[]>('/buy-orders');
          const order = orders.find((o) => o.buyOrderId === orderId);
          if (order && order.status !== prevStatusRef.current) {
            prevStatusRef.current = order.status;
            addLog(`[System] Order #${orderId} status: ${order.status}${order.operationNote ? ` — ${order.operationNote}` : ''}`);
            if (order.status !== 'pending') {
              setRunning(false);
            }
          }
        } else {
          const orders = await api.get<SellOrderResponse[]>('/sell-orders');
          const order = orders.find((o) => o.sellOrderId === orderId);
          if (order && order.status !== prevStatusRef.current) {
            prevStatusRef.current = order.status;
            addLog(`[System] Order #${orderId} status: ${order.status}${order.operationNote ? ` — ${order.operationNote}` : ''}`);
            if (order.status !== 'pending') {
              setRunning(false);
            }
          }
        }

        // Check executions
        const executions = await api.get<ExecutionResponse[]>('/executions');
        const related = executions.filter(
          (e) => direction === 'SELL' ? e.sellOrderId === orderId : e.relatedBy === 'buy_order'
        );
        for (const exec of related) {
          addLog(`[System] Execution ${exec.executionId}: ${exec.status}${exec.failureReason ? ` — ${exec.failureReason}` : ''}`);
        }
      } catch {
        // silently ignore polling errors
      }
    };

    intervalRef.current = setInterval(poll, 5000);
    // Initial poll
    poll();

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [running, orderId, direction, addLog]);

  return { logs, running, start, clear };
}
