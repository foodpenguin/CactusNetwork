'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  BuyOrderRequest,
  BuyOrderResponse,
  ExecutionResponse,
  SellOrderRequest,
  SellOrderResponse,
} from '@/types/api';

export function useBuyOrder() {
  return useMutation({
    mutationFn: (data: BuyOrderRequest) =>
      api.post<BuyOrderResponse>('/buy-orders', data),
  });
}

export function useSellOrder() {
  return useMutation({
    mutationFn: (data: SellOrderRequest) =>
      api.post<SellOrderResponse>('/sell-orders', data),
  });
}

export function useBuyOrders() {
  return useQuery({
    queryKey: ['buy-orders'],
    queryFn: () => api.get<BuyOrderResponse[]>('/buy-orders'),
  });
}

export function useSellOrders() {
  return useQuery({
    queryKey: ['sell-orders'],
    queryFn: () => api.get<SellOrderResponse[]>('/sell-orders'),
  });
}

export function useExecutions() {
  return useQuery({
    queryKey: ['executions'],
    queryFn: () => api.get<ExecutionResponse[]>('/executions'),
  });
}
