import { api, setToken } from './api';
import type { LoginResponse } from '@/types/api';

/**
 * 錢包簽名登入流程：
 * 1. GET /auth/nonce 取得 nonce
 * 2. 使用者錢包對 nonce 做 personal_sign
 * 3. POST /login 送 address + signature
 * 4. 後端驗證後核發 Bearer token（帳號不存在時自動建立）
 */
export async function walletLogin(
  address: string,
  signMessage: (args: { message: string }) => Promise<string>,
): Promise<LoginResponse> {
  const nonceRes = await api.get<{ nonce: string }>(`/auth/nonce?address=${address}`);
  const signature = await signMessage({ message: nonceRes.nonce });
  const loginRes = await api.post<LoginResponse>('/login', {
    address,
    signature,
  });
  setToken(loginRes.accessToken);
  return loginRes;
}
