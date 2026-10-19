import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { formatKobo } from '../../lib/format';
import { ErrorAlert, Loading, PageHeader } from '../../lib/ui';

/** The platform at a glance: schools, pupils, money through SchoolPay, and subscription income. */
export function PlatformOverview() {
  const overview = useQuery({
    queryKey: ['platform-overview'],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/overview')),
    staleTime: 0,
  });
  if (overview.isPending) return <Loading />;
  if (overview.isError) return <ErrorAlert error={overview.error} />;
  const o = overview.data;
  return (
    <>
      <PageHeader title="Platform overview" subtitle="Every school on SchoolPay" />
      <h2 className="section-title">Schools</h2>
      <div className="stats">
        <Card label="Active schools" value={String(o.schools_active)} note={<Link to="/console/platform/schools">See all schools</Link>} />
        <Card
          label="Waiting to confirm"
          value={String(o.schools_pending)}
          note={o.schools_pending > 0 ? <Link to="/console/platform/schools?status=pending">Signed up, email not confirmed</Link> : 'Signed up, email not confirmed'}
        />
        <Card label="Suspended" value={String(o.schools_suspended)} />
        <Card label="New sign-ups" value={String(o.signups_30_days)} note="In the last 30 days" />
        <Card label="Pupils" value={o.pupils.toLocaleString('en-NG')} note="Enrolled this term at active schools" />
      </div>
      <h2 className="section-title">Money parents paid schools through SchoolPay</h2>
      <div className="stats">
        <Card label="This month" value={formatKobo(o.processed_this_month_kobo)} note={`${o.payments_this_month} payments`} />
        <Card label="Last month" value={formatKobo(o.processed_last_month_kobo)} />
      </div>
      <h2 className="section-title">Subscriptions</h2>
      <div className="stats">
        <Card label="Billed" value={formatKobo(o.subscription_billed_kobo)} />
        <Card label="Received" value={formatKobo(o.subscription_paid_kobo)} tone="ok" />
        <Card
          label="Outstanding"
          value={formatKobo(o.subscription_outstanding_kobo)}
          tone={o.subscription_outstanding_kobo > 0 ? 'warn' : undefined}
          note={<Link to="/console/platform/billing">Subscription bills</Link>}
        />
        <Card
          label="Overdue"
          value={formatKobo(o.subscription_overdue_kobo)}
          tone={o.subscription_overdue_kobo > 0 ? 'warn' : undefined}
          note={o.subscription_overdue_kobo > 0 ? <Link to="/console/platform/billing?status=overdue">See overdue bills</Link> : undefined}
        />
      </div>
    </>
  );
}

export function Card({ label, value, note, tone }: { label: string; value: string; note?: React.ReactNode; tone?: 'ok' | 'warn' }) {
  return (
    <div className={`card stat ${tone ?? ''}`}>
      <div className="muted small">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="muted small">{note}</div>}
    </div>
  );
}
