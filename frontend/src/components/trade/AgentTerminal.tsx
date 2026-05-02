'use client';

import { useEffect, useRef } from 'react';
import { useLanguage } from '@/contexts/LanguageContext';

export interface LogLine {
  timestamp: string;
  text: string;
}

interface Props {
  logs: LogLine[];
  running: boolean;
}

export function AgentTerminal({ logs, running }: Props) {
  const { t } = useLanguage();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div
      className="rounded-2xl flex flex-col h-full overflow-hidden"
      style={{ background: '#1c1c1c', border: '1px solid #333', minHeight: 480 }}
    >
      {/* Title bar */}
      <div
        className="flex items-center gap-2 px-4 py-3 border-b text-xs font-mono"
        style={{ borderColor: '#333', color: '#888' }}
      >
        <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: running ? '#4caf7d' : '#555' }} />
        <span style={{ color: '#f2a8b4' }}>CactusNetwork Agent Console</span>
        {running && <span className="ml-auto animate-pulse text-xs">{t.agentTerminal.running}</span>}
      </div>

      {/* Log body */}
      <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-1" style={{ color: '#f2a8b4' }}>
        {logs.length === 0 ? (
          <div style={{ color: '#555' }}>
            {t.agentTerminal.waiting}{'\n'}
            {t.agentTerminal.waitingDesc}
          </div>
        ) : (
          logs.map((log, i) => {
            const isSystem = log.text.startsWith('[System]') || log.text.startsWith('[系統]');
            const isStatus = log.text.includes('pending') || log.text.includes('confirmed') || log.text.includes('failed');
            let color = '#f2a8b4';
            if (isSystem) color = '#7dd3fc';
            if (isStatus && log.text.includes('confirmed')) color = '#86efac';
            if (isStatus && log.text.includes('failed')) color = '#fca5a5';

            return (
              <div key={i} className="leading-relaxed">
                <span style={{ color: '#555' }}>[{log.timestamp}] </span>
                <span style={{ color }}>{log.text}</span>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
