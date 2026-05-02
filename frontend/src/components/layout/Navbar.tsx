'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { useAccount, useConnect, useDisconnect } from 'wagmi';
import { injected } from 'wagmi/connectors';
import cactusLogo from '../../../public/cactus.png';
import { useLanguage } from '@/contexts/LanguageContext';

function WalletButton() {
  const { address, isConnected } = useAccount();
  const { connect } = useConnect();
  const { disconnect } = useDisconnect();
  const { t } = useLanguage();

  const [level, setLevel] = useState<string | null>(null);

  useEffect(() => {
    if (isConnected && address) {
      import('@/lib/api').then(({ api }) => {
        api.get<{ accountLevel: string }>('/account/me')
           .then(res => setLevel(res.accountLevel))
           .catch(() => setLevel(null));
      });
    } else {
      setLevel(null);
    }
  }, [isConnected, address]);

  if (isConnected && address) {
    return (
      <div className="flex items-center gap-2">
        {level && level !== 'free' && (
          <span
            className="px-2 py-1 rounded-md text-xs font-bold uppercase"
            style={{
              background: level === 'max' ? '#f9d0d8' : '#fde8ec',
              color: level === 'max' ? '#c0475a' : '#e07585',
              border: `1px solid ${level === 'max' ? '#e07585' : '#f2a8b4'}`,
            }}
          >
            {level}
          </span>
        )}
        <button
          onClick={() => disconnect()}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
          style={{ background: '#f2a8b4', color: '#1c1c1c' }}
        >
          {address.slice(0, 6)}...{address.slice(-4)}
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => connect({ connector: injected() })}
      className="px-4 py-2 rounded-lg text-sm font-medium transition-colors hover:opacity-80"
      style={{ background: '#f2a8b4', color: '#1c1c1c' }}
    >
      {t.navbar.connectWallet}
    </button>
  );
}

export function Navbar() {
  const pathname = usePathname();
  const { t, lang, toggleLang } = useLanguage();

  const navLinks = [
    { href: '/', label: t.navbar.home },
    { href: '/trade', label: t.navbar.trade },
    { href: '/dashboard', label: t.navbar.dashboard },
    { href: '/pricing', label: t.navbar.pricing },
  ];

  return (
    <nav className="sticky top-0 z-50 border-b" style={{ background: '#faf5f0', borderColor: '#e8ddd5' }}>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2">
          <Image src={cactusLogo} alt="CactusNetwork" width={36} height={36} className="rounded-lg" />
          <span className="font-bold text-lg" style={{ color: '#1c1c1c' }}>cactus</span>
          <span className="text-sm font-medium" style={{ color: '#e07585' }}>—network—</span>
        </Link>

        {/* Nav links */}
        <div className="hidden sm:flex items-center gap-6">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm font-medium transition-colors"
              style={{
                color: pathname === link.href ? '#e07585' : '#1c1c1c',
              }}
            >
              {link.label}
            </Link>
          ))}
        </div>

        {/* Right: lang toggle + wallet */}
        <div className="flex items-center gap-2">
          <button
            onClick={toggleLang}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors hover:opacity-80"
            style={{ borderColor: '#e8ddd5', color: '#e07585', background: '#faf5f0' }}
            aria-label="Switch language"
          >
            {lang === 'zh' ? 'EN' : '中'}
          </button>
          <WalletButton />
        </div>
      </div>
    </nav>
  );
}
