'use client';

import { useEffect, useState } from 'react';
import { useAccount } from 'wagmi';
import { registerOrLogin } from '@/lib/auth';
import { getToken } from '@/lib/api';

export function useAuth() {
  const { address, isConnected } = useAccount();
  const [token, setToken] = useState<string | null>(getToken());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isConnected && address && !token) {
      setLoading(true);
      setError(null);
      registerOrLogin(address)
        .then((t) => setToken(t))
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    }
    if (!isConnected) {
      setToken(null);
    }
  }, [isConnected, address, token]);

  return { token, loading, error, isAuthenticated: !!token };
}
