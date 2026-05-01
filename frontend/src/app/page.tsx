'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useLanguage } from '@/contexts/LanguageContext';

const FEATURE_ICONS = ['TWAP', 'OTC'];

function AnimatedCounter({ target, prefix = '', suffix = '' }: { target: number; prefix?: string; suffix?: string }) {
  const [value, setValue] = useState(0);

  useEffect(() => {
    const step = target / 60;
    let current = 0;
    const timer = setInterval(() => {
      current += step;
      if (current >= target) {
        setValue(target);
        clearInterval(timer);
      } else {
        setValue(Math.floor(current));
      }
    }, 16);
    return () => clearInterval(timer);
  }, [target]);

  return (
    <span>
      {prefix}{value.toLocaleString()}{suffix}
    </span>
  );
}

export default function HomePage() {
  const { t } = useLanguage();
  return (
    <div className="flex flex-col">
      {/* Hero */}
      <section className="flex flex-col items-center justify-center text-center px-4 py-24 gap-6">
        <div
          className="inline-block text-xs font-semibold px-3 py-1 rounded-full mb-2"
          style={{ background: '#fde8ec', color: '#e07585' }}
        >
          {t.landing.badge}
        </div>

        <h1 className="text-5xl sm:text-6xl font-bold leading-tight" style={{ color: '#1c1c1c' }}>
          {t.landing.heroTitle}<br />
          <span style={{ color: '#e07585' }}>{t.landing.heroSubtitle}</span> {t.landing.heroTagline}
        </h1>

        <p className="text-lg max-w-xl" style={{ color: '#555' }}>
          {t.landing.heroDesc}
        </p>

        <div className="flex flex-wrap gap-8 justify-center my-4">
          <div className="text-center">
            <div className="text-3xl font-bold" style={{ color: '#e07585' }}>
              <AnimatedCounter target={2480000} prefix="$" suffix=" USDC" />
            </div>
            <div className="text-sm mt-1" style={{ color: '#888' }}>{t.landing.statSavedLabel}</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-bold" style={{ color: '#e07585' }}>
              <AnimatedCounter target={12400} suffix={t.landing.statOrdersSuffix} />
            </div>
            <div className="text-sm mt-1" style={{ color: '#888' }}>{t.landing.statOrdersLabel}</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-bold" style={{ color: '#e07585' }}>
              0.00%
            </div>
            <div className="text-sm mt-1" style={{ color: '#888' }}>{t.landing.statSlippageLabel}</div>
          </div>
        </div>

        <Link
          href="/trade"
          className="px-8 py-3 rounded-xl font-semibold text-base transition-all hover:opacity-90 active:scale-95"
          style={{ background: '#f2a8b4', color: '#1c1c1c' }}
        >
          {t.landing.startTrading}
        </Link>
      </section>

      <div className="border-t" style={{ borderColor: '#e8ddd5' }} />

      <section className="max-w-5xl mx-auto px-4 py-20 w-full">
        <h2 className="text-2xl font-bold text-center mb-12" style={{ color: '#1c1c1c' }}>
          {t.landing.sectionTitle}
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 max-w-2xl mx-auto gap-6 w-full">
          {t.landing.features.map((f, i) => (
            <div
              key={f.title}
              className="rounded-2xl p-6 flex flex-col gap-3"
              style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
            >
              <span className="text-3xl">{FEATURE_ICONS[i]}</span>
              <h3 className="font-semibold text-base" style={{ color: '#1c1c1c' }}>{f.title}</h3>
              <p className="text-sm leading-relaxed" style={{ color: '#666' }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section
        className="mx-4 mb-16 rounded-3xl p-10 flex flex-col sm:flex-row items-center justify-between gap-6"
        style={{ background: '#fde8ec' }}
      >
        <div>
          <h3 className="text-xl font-bold" style={{ color: '#1c1c1c' }}>{t.landing.ctaTitle}</h3>
          <p className="text-sm mt-1" style={{ color: '#888' }}>{t.landing.ctaDesc}</p>
        </div>
        <Link
          href="/trade"
          className="px-6 py-3 rounded-xl font-semibold text-sm transition-all hover:opacity-90"
          style={{ background: '#e07585', color: '#fff' }}
        >
          {t.landing.ctaButton}
        </Link>
      </section>
    </div>
  );
}
