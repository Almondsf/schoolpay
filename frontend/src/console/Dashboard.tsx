import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { formatDate, formatKobo } from '../lib/format';
import { Empty, ErrorAlert, Loading, PageHeader } from '../lib/ui';
import { useSessions } from './hooks';

type Overview = Schemas['OverviewOut'];

export const METHOD_LABELS: Record<Schemas['PaymentMethod'], string> = {
  virtual_account: 'Transfer to student account',
  card: 'Card',
  teller: 'Bank teller',
  cash: 'Cash',
  pos: 'POS',
  bank_transfer: 'Transfer to school account',
};

export const AGING_LABELS: Record<Schemas['AgingBucket'], string> = {
  not_due: 'Not yet due',
  '1_30': '1–30 days late',
  '31_60': '31–60 days late',
  '61_90': '61–90 days late',
  over_90: 'Over 90 days late',
};

/** Whole percent, rounded down so 99.6% never shows as 100% while money is still owed. */
export function percent(part: number, whole: number): number {
  return whole > 0 ? Math.floor((part * 100) / whole) : 0;
}

/** Collections at a glance: billed, collected and outstanding for a term, and how money is coming in. */
export function Dashboard() {
  const { user, can } = useAuth();
  const first = user?.full_name.split(' ')[0] ?? '';
  if (!can('reports.view')) {
    return (
      <>
        <PageHeader title={`Welcome, ${first}`} subtitle={user?.school?.name} />
        <div className="card">
          <p>Use the menu to manage students, classes and the school calendar.</p>
        </div>
      </>
    );
  }
  return <MoneyDashboard first={first} schoolName={user?.school?.name ?? ''} />;
}

function MoneyDashboard({ first, schoolName }: { first: string; schoolName: string }) {
  const [termId, setTermId] = useState('');
  const sessions = useSessions();
  const overview = useQuery({
    queryKey: ['reports-overview', termId],
    staleTime: 0, // money figures are re-read every time the page is shown
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/reports/overview', {
          params: { query: { term_id: termId || undefined } },
        }),
      ),
  });
  const terms = (sessions.data ?? []).flatMap((s) =>
    s.terms.map((t) => ({
      id: t.id,
      label: `${s.name} ${t.label}`,
      current: t.is_current,
    })),
  );

  return (
    <>
      <PageHeader
        title={`Welcome, ${first}`}
        subtitle={schoolName}
        actions={
          terms.length > 0 && (
            <select aria-label="Term" value={termId || overview.data?.term_id || ''} onChange={(e) => setTermId(e.target.value)}>
              {terms.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                  {t.current ? ' (current)' : ''}
                </option>
              ))}
            </select>
          )
        }
      />
      {overview.isPending ? <Loading /> : overview.isError ? <ErrorAlert error={overview.error} /> : <Figures data={overview.data} />}
    </>
  );
}

function Figures({ data }: { data: Overview }) {
  if (data.invoices === 0) {
    return (
      <Empty>
        No bills for {data.term_label} yet. <Link to="/console/billing">Generate the term's bills</Link> and this page fills in as payments
        arrive.
      </Empty>
    );
  }
  const rate = percent(data.collected_kobo, data.billed_kobo);
  return (
    <>
      <h2 className="section-title">{data.term_label}</h2>
      <div className="stats">
        <Stat label="Billed" value={formatKobo(data.billed_kobo)} note={`${data.invoices} bills`} />
        <Stat label="Collected" value={formatKobo(data.collected_kobo)} tone="ok">
          <Progress value={rate} label={`${rate}% of the term's bills`} />
        </Stat>
        <Stat
          label="Outstanding"
          value={formatKobo(data.outstanding_kobo)}
          tone={data.outstanding_kobo > 0 ? 'warn' : undefined}
          note={<Link to="/console/reports">{data.students_owing} students owe</Link>}
        />
        <Stat
          label="Received today"
          value={formatKobo(data.received_today_kobo)}
          note={`${formatKobo(data.received_7_days_kobo)} in the last 7 days`}
        />
      </div>
      <div className="stats">
        <Stat
          label="Older debts"
          value={formatKobo(data.older_outstanding_kobo)}
          note="Earlier terms, balances brought forward and one-off charges"
          small
        />
        <Stat label="Credit held" value={formatKobo(data.credit_kobo)} note="Paid ahead; used on the next bill" small />
        <Stat
          label="Awaiting approval"
          value={formatKobo(data.pending_approval_kobo)}
          note={
            data.pending_approval > 0 ? (
              <Link to="/console/payments">{data.pending_approval} cash or teller entries to check</Link>
            ) : (
              'Nothing waiting'
            )
          }
          tone={data.pending_approval > 0 ? 'warn' : undefined}
          small
        />
      </div>

      <div className="grid-2">
        <section className="card" aria-label="Money received per day">
          <h2>Money received per day</h2>
          <p className="muted small">
            {formatKobo(data.received_this_term_kobo)} since {formatDate(data.term_start)}, after any reversals.
          </p>
          <DailyChart days={data.by_day} />
        </section>
        <section className="card" aria-label="How parents paid">
          <h2>How parents paid</h2>
          {data.by_method.length === 0 ? (
            <p className="muted">No payments this term yet.</p>
          ) : (
            <ul className="bars">
              {data.by_method.map((m) => (
                <li key={m.method}>
                  <div className="row between small">
                    <span>{METHOD_LABELS[m.method]}</span>
                    <span>
                      {formatKobo(m.amount_kobo)} <span className="muted">· {m.payments}</span>
                    </span>
                  </div>
                  <Progress value={percent(m.amount_kobo, data.received_this_term_kobo)} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card table-card" aria-label="By class">
        <div className="row between card-pad">
          <h2>By class</h2>
          <Link to="/console/reports" className="small">
            Debtors list →
          </Link>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th className="num">Billed</th>
                <th className="num">Collected</th>
                <th className="num">Outstanding</th>
                <th style={{ width: '22%' }}>Collected</th>
                <th className="num">Owing</th>
              </tr>
            </thead>
            <tbody>
              {data.by_class.map((c) => {
                const classRate = percent(c.collected_kobo, c.billed_kobo);
                return (
                  <tr key={c.class_level_id ?? 'none'}>
                    <td>{c.class_name}</td>
                    <td className="num">{formatKobo(c.billed_kobo)}</td>
                    <td className="num">{formatKobo(c.collected_kobo)}</td>
                    <td className="num">{formatKobo(c.outstanding_kobo)}</td>
                    <td>
                      <Progress value={classRate} label={`${classRate}%`} />
                    </td>
                    <td className="num">
                      {c.students_owing} of {c.invoices}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card" aria-label="How late the debts are">
        <h2>How late the debts are</h2>
        <p className="muted small">Everything still owed, by how long ago each bill was due.</p>
        <div className="aging">
          {data.aging.map((a) => (
            <div
              key={a.bucket}
              className={`aging-cell ${a.amount_kobo === 0 || a.bucket === 'not_due' ? '' : a.bucket === '1_30' ? 'warn' : 'danger'}`}
            >
              <div className="small muted">{AGING_LABELS[a.bucket]}</div>
              <div className="stat-value">{formatKobo(a.amount_kobo)}</div>
              <div className="small muted">{a.invoices} bills</div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
  small,
  children,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
  tone?: 'ok' | 'warn';
  small?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={`card stat ${tone ?? ''} ${small ? 'small-stat' : ''}`}>
      <div className="muted small">{label}</div>
      <div className="stat-value">{value}</div>
      {children}
      {note && <div className="muted small">{note}</div>}
    </div>
  );
}

function Progress({ value, label }: { value: number; label?: string }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="progress-row">
      <div className="progress" role="progressbar" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className="progress-fill" style={{ width: `${clamped}%` }} />
      </div>
      {label && <span className="small muted nowrap">{label}</span>}
    </div>
  );
}

/** Bars per day; negative days (reversals) dip below the line. Plain SVG, no chart library. */
function DailyChart({ days }: { days: Schemas['DayTotalOut'][] }) {
  if (days.length === 0) return <p className="muted">No payments yet.</p>;
  const width = 600;
  const height = 160;
  const max = Math.max(...days.map((d) => d.amount_kobo), 1);
  const min = Math.min(...days.map((d) => d.amount_kobo), 0);
  const range = max - min;
  const zero = height * (max / range);
  const step = width / days.length;
  const best = days.reduce((a, b) => (b.amount_kobo > a.amount_kobo ? b : a));
  return (
    <>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label={`Money received on ${days.length} days`}>
        <line x1={0} x2={width} y1={zero} y2={zero} className="chart-axis" />
        {days.map((d, i) => {
          const h = (Math.abs(d.amount_kobo) / range) * height;
          return (
            <rect
              key={d.day}
              x={i * step + step * 0.15}
              width={step * 0.7}
              y={d.amount_kobo >= 0 ? zero - h : zero}
              height={Math.max(h, 1)}
              className={d.amount_kobo >= 0 ? 'chart-bar' : 'chart-bar negative'}
            >
              <title>{`${formatDate(d.day)}: ${formatKobo(d.amount_kobo)} (${d.payments} payments)`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="row between small muted">
        <span>{formatDate(days[0]!.day)}</span>
        <span>
          Best day: {formatDate(best.day)}, {formatKobo(best.amount_kobo)}
        </span>
        <span>{formatDate(days[days.length - 1]!.day)}</span>
      </div>
    </>
  );
}
