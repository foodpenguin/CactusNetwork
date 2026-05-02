'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useLanguage } from '@/contexts/LanguageContext';
import { useWalletClient, usePublicClient } from 'wagmi';
import { approvePriorityFee, payPriorityFee, checkAllowance, PLAN_AMOUNTS } from '@/lib/pricing';
import { api } from '@/lib/api';

const PLAN_KEYS = ['free', 'plus', 'max'] as const;
type PlanKey = (typeof PLAN_KEYS)[number];

const PLAN_COLORS: Record<PlanKey, { border: string; accent: string; bg: string }> = {
  free: { border: '#e8ddd5', accent: '#888', bg: '#faf5f0' },
  plus: { border: '#f2a8b4', accent: '#e07585', bg: '#fde8ec' },
  max: { border: '#e07585', accent: '#c0475a', bg: '#f9d0d8' },
};

export default function PricingPage() {
  const { t } = useLanguage();
  const { address, isAuthenticated } = useAuth();
  const { data: walletClient } = useWalletClient();
  const publicClient = usePublicClient();
  const [currentLevel, setCurrentLevel] = useState<string>('free');
  const [step, setStep] = useState<'idle' | 'approving' | 'paying' | 'verifying'>('idle');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Fetch current level on mount
  useState(() => {
    if (isAuthenticated) {
      api.get<{ accountLevel: string }>('/account/me').then((res) => {
        setCurrentLevel(res.accountLevel);
      }).catch(() => {});
    }
  });

  async function handleUpgrade(plan: PlanKey) {
    if (!walletClient || !publicClient || !address || plan === 'free') return;
    const amount = PLAN_AMOUNTS[plan];
    if (!amount) return;

    setError('');
    setSuccess('');

    try {
      const rawAmount = BigInt(amount) * BigInt(10 ** 6);
      const allowance = await checkAllowance({ publicClient, user: address });
      
      if (allowance < rawAmount) {
        // Step 1: Approve USDC
        setStep('approving');
        await approvePriorityFee({ walletClient, user: address, amountUsdc: amount });
      }

      // Step 2: Pay
      setStep('paying');
      const txHash = await payPriorityFee({ walletClient, user: address, amountUsdc: amount });

      // Step 3: Backend verification
      setStep('verifying');
      const result = await api.post<{ message: string; accountLevel: string }>('/account/upgrade', {
        tx_hash: txHash,
        target_level: plan,
      });

      setCurrentLevel(result.accountLevel);
      setSuccess(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : '升級失敗');
    } finally {
      setStep('idle');
    }
  }

  const stepLabels: Record<string, string> = {
    approving: t.pricing.approving,
    paying: t.pricing.paying,
    verifying: t.pricing.verifying,
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-12">
      <div className="text-center mb-12">
        <h1 className="text-3xl font-bold" style={{ color: '#1c1c1c' }}>{t.pricing.title}</h1>
        <p className="text-sm mt-2" style={{ color: '#888' }}>{t.pricing.desc}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {PLAN_KEYS.map((plan) => {
          const planData = t.pricing[plan] as { name: string; price: string; features: string[] };
          const colors = PLAN_COLORS[plan];
          const isCurrent = currentLevel === plan;
          const isUpgradable = plan !== 'free' && !isCurrent;

          return (
            <div
              key={plan}
              className="rounded-2xl p-6 flex flex-col gap-4 transition-all"
              style={{
                background: '#fff',
                border: `2px solid ${isCurrent ? colors.accent : colors.border}`,
                boxShadow: isCurrent ? `0 4px 20px ${colors.accent}33` : '0 2px 12px rgba(0,0,0,0.06)',
              }}
            >
              <div>
                <h3 className="text-lg font-bold" style={{ color: colors.accent }}>{planData.name}</h3>
                <div className="text-2xl font-bold mt-1" style={{ color: '#1c1c1c' }}>{planData.price}</div>
              </div>

              <ul className="flex flex-col gap-2 flex-1">
                {planData.features.map((f, i) => (
                  <li key={i} className="text-sm flex items-start gap-2">
                    <span style={{ color: colors.accent }}>✓</span>
                    <span style={{ color: '#555' }}>{f}</span>
                  </li>
                ))}
              </ul>

              {isCurrent ? (
                <div
                  className="py-2.5 rounded-xl text-sm font-semibold text-center"
                  style={{ background: colors.bg, color: colors.accent }}
                >
                  {t.pricing.current}
                </div>
              ) : isUpgradable ? (
                <button
                  onClick={() => handleUpgrade(plan)}
                  disabled={!isAuthenticated || step !== 'idle'}
                  className="py-2.5 rounded-xl text-sm font-semibold transition-all hover:opacity-90 disabled:opacity-40"
                  style={{ background: colors.accent, color: '#fff' }}
                >
                  {step !== 'idle'
                    ? stepLabels[step] || '...'
                    : t.pricing.upgrade}
                </button>
              ) : (
                <div className="py-2.5" />
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <div className="mt-6 text-center text-sm px-4 py-3 rounded-xl" style={{ background: '#fee2e2', color: '#991b1b' }}>
          {error}
        </div>
      )}
      {success && (
        <div className="mt-6 text-center text-sm px-4 py-3 rounded-xl" style={{ background: '#dcfce7', color: '#166534' }}>
          {success}
        </div>
      )}
    </div>
  );
}
