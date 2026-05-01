'use client';

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { useLanguage } from '@/contexts/LanguageContext';

interface DataPoint {
  n: number;
  price: number;
}

interface Props {
  data: DataPoint[];
  basePrice: number;
}

export function PriceChart({ data, basePrice }: Props) {
  const { t } = useLanguage();
  return (
    <div
      className="rounded-2xl p-5"
      style={{ background: '#fff', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
    >
      <div className="text-sm font-semibold mb-4" style={{ color: '#1c1c1c' }}>
        {t.priceChart.title}
      </div>
      {data.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-sm" style={{ color: '#aaa' }}>
          {t.priceChart.noData}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f5f0eb" />
            <XAxis
              dataKey="n"
              tick={{ fontSize: 11, fill: '#aaa' }}
              label={{ value: t.priceChart.xLabel, position: 'insideBottom', offset: -4, fontSize: 11, fill: '#aaa' }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#aaa' }}
              domain={['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{ background: '#fff', border: '1px solid #e8ddd5', borderRadius: 8, fontSize: 12 }}
              labelFormatter={(v) => t.priceChart.tooltipChunk(Number(v))}
              formatter={(v) => [`$${Number(v).toFixed(2)} USDC`, t.priceChart.tooltipPrice]}
            />
            <ReferenceLine
              y={basePrice}
              stroke="#e07585"
              strokeDasharray="4 2"
              label={{ value: t.priceChart.baseline, fill: '#e07585', fontSize: 11 }}
            />
            <Line
              type="monotone"
              dataKey="price"
              stroke="#f2a8b4"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#e07585' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
