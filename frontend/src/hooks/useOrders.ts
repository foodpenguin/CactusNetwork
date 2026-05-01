'use client';

import { useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { BuyOrderRequest, BuyOrderResponse, SellOrderRequest, SellOrderResponse } from '@/types/api';

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
