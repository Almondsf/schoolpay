import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { BRAND } from '../../lib/brand';
import { formatKobo } from '../../lib/format';
import { ErrorAlert, Loading, Tabs } from '../../lib/ui';

type Month = Schemas['TrendMonthOut'];
type Measure = 'subscription' | 'processed' | 'schools';

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function monthLabel(month: string): string {
  const [year, number] = month.split('-');
  return `${MONTH_NAMES[Number(number) - 1]} ${year!.slice(2)}`;
}

const MEASURES: Record<Measure, { value: (m: Month) => number; show: (n: number) => string; sum: string }> = {
  subscription: { value: (m) => m.subscription_received_kobo, show: formatKobo, sum: 'received from schools' },
  processed: { value: (m) => m.processed_kobo, show: formatKobo, sum: `paid by parents through ${BRAND}` },
  schools: { value: (m) => m.schools_joined - m.schools_suspended, show: (n) => (n > 0 ? `+${n}` : String(n)), sum: 'schools, net' },
};

/** The platform's last 12 months: your income, money through WardPay, schools joining. Plain SVG bars. */
export function PlatformTrends() {
  const [measure, setMeasure] = useState<Measure>('subscription');
  const trends = useQuery({
    queryKey: ['platform-trends'],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/trends', { params: { query: { months: 12 } } })),
  });
  if (trends.isPending) return <Loading />;
  if (trends.isError) return <ErrorAlert error={trends.error} />;
  const months = trends.data;
  const { value, show, sum } = MEASURES[measure];
  const values = months.map(value);
  const total = values.reduce((a, b) => a + b, 0);
  const width = 600;
  const height = 150;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const zero = height * (max / range);
  const step = width / months.length;
  return (
    <section className="card" aria-label="Last 12 months">
      <Tabs
        label="Show"
        value={measure}
        onChange={setMeasure}
        tabs={[
          { key: 'subscription', label: 'Your income' },
          { key: 'processed', label: `Money through ${BRAND}` },
          { key: 'schools', label: 'Schools' },
        ]}
      />
      <p className="small">
        Last 12 months: <strong>{show(total)}</strong> {sum}
        {measure === 'schools' && (
          <span className="muted">
            {' '}
            ({months.reduce((a, m) => a + m.schools_joined, 0)} joined, {months.reduce((a, m) => a + m.schools_suspended, 0)} suspended)
          </span>
        )}
      </p>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label={`${sum} per month`}>
        <line x1={0} x2={width} y1={zero} y2={zero} className="chart-axis" />
        {months.map((m, i) => {
          const v = values[i]!;
          const h = (Math.abs(v) / range) * height;
          return (
            <rect
              key={m.month}
              x={i * step + step * 0.18}
              width={step * 0.64}
              y={v >= 0 ? zero - h : zero}
              height={Math.max(h, 1)}
              className={v >= 0 ? 'chart-bar' : 'chart-bar negative'}
            >
              <title>{`${monthLabel(m.month)}: ${show(v)}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="month-labels small muted">
        {months.map((m) => (
          <span key={m.month}>{monthLabel(m.month)}</span>
        ))}
      </div>
    </section>
  );
}
