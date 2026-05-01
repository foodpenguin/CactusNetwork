import { api, setToken } from './api';
import type { AccountResponse, LoginResponse } from '@/types/api';

export async function registerOrLogin(address: string): Promise<string> {
  const accountName = `${address.slice(0, 6)}...${address.slice(-4)}`;
  const password = address;
  const publicKey = address;

  // 先嘗試登入
  try {
    const loginRes = await api.post<LoginResponse>('/login', {
      account_name: accountName,
      password,
    });
    setToken(loginRes.accessToken);
    return loginRes.accessToken;
  } catch {
    // 登入失敗表示帳號不存在，先建立帳號
  }

  await api.post<AccountResponse>('/accounts', {
    account_name: accountName,
    password,
    public_key: publicKey,
  });

  const loginRes = await api.post<LoginResponse>('/login', {
    account_name: accountName,
    password,
  });
  setToken(loginRes.accessToken);
  return loginRes.accessToken;
}
