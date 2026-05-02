'use client';

import { useCallback, useEffect, useState } from 'react';
import { useAccount, useSignMessage } from 'wagmi';
import { walletLogin } from '@/lib/auth';
import { getToken, clearToken } from '@/lib/api';

export function useAuth() {
  const { address, isConnected } = useAccount();
  const { signMessageAsync } = useSignMessage();
  const [token, setToken] = useState<string | null>(getToken());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = useCallback(async () => {
    if (!address) return;
    setLoading(true);
    setError(null);
    try {
      const res = await walletLogin(address, async ({ message }) => {
        return await signMessageAsync({ message });
      });
      setToken(res.accessToken);
    } catch (e) {
      setError(e instanceof Error ? e.message : '登入失敗');
    } finally {
      setLoading(false);
    }
  }, [address, signMessageAsync]);

  useEffect(() => {
    if (isConnected && address && !token) {
      login();
    }
    if (!isConnected) {
      clearToken();
      setToken(null);
    }
  }, [isConnected, address, token, login]);

  return { address, token, loading, error, isAuthenticated: !!token, login };
}
