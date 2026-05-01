'use client';

import { useCallback, useRef, useState } from 'react';
import { useLanguage } from '@/contexts/LanguageContext';

export interface LogLine {
  timestamp: string;
  text: string;
}

function timestamp() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false });
}

export function useAgentLog() {
  const { t } = useLanguage();
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const start = useCallback((slippage: number, splits: number, chunkSize: number) => {
    // clear previous
    timerRef.current.forEach(clearTimeout);
    timerRef.current = [];
    setLogs([]);
    setRunning(true);

    const lines = t.agentLogs.build(slippage, splits, chunkSize);
    lines.forEach((text, i) => {
      const timer = setTimeout(() => {
        setLogs((prev) => [...prev, { timestamp: timestamp(), text }]);
        if (i === lines.length - 1) setRunning(false);
      }, (i + 1) * 900);
      timerRef.current.push(timer);
    });
  }, [t]);

  const clear = useCallback(() => {
    timerRef.current.forEach(clearTimeout);
    setLogs([]);
    setRunning(false);
  }, []);

  return { logs, running, start, clear };
}
