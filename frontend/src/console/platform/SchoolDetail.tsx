import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { ROLE_LABELS, formatDate, formatDateTime, formatKobo } from '../../lib/format';
import { ErrorAlert, Loading, PageHeader, StatusBadge } from '../../lib/ui';
import { InvoiceTable } from './Billing';
import { Card } from './Overview';
import { ChangeStatus } from './Schools';

/** One school, as support sees it: figures, staff, plan and subscription. No pupils' or parents' details. */
export function PlatformSchoolDetail() {
  const { schoolId = '' } = useParams();
  const queryClient = useQueryClient();
  const [changing, setChanging] = useState(false);
  const detail = useQuery({
    queryKey: ['platform-school', schoolId],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/schools/{school_id}', { params: { path: { school_id: schoolId } } })),
  });
  const plans = useQuery({ queryKey: ['platform-plans'], queryFn: async () => unwrap(await api.GET('/api/v1/platform/plans')) });
  const invoices = useQuery({
    queryKey: ['platform-invoices', 'school', schoolId],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/billing/invoices', { params: { query: { school_id: schoolId, limit: 50 } } })),
  });
  const setPlan = useMutation({
    mutationFn: async (planId: string) =>
      unwrap(await api.PUT('/api/v1/platform/schools/{school_id}/plan', { params: { path: { school_id: schoolId } }, body: { plan_id: planId } })),
    onSuccess: (data) => {
      queryClient.setQueryData(['platform-school', schoolId], data);
      void queryClient.invalidateQueries({ queryKey: ['platform-schools'] });
      void queryClient.invalidateQueries({ queryKey: ['platform-plans'] });
    },
  });

  if (detail.isPending) return <Loading />;
  if (detail.isError) return <ErrorAlert error={detail.error} />;
  const d = detail.data;
  const s = d.school;
  const defaultPlan = plans.data?.find((p) => p.is_default);
  return (
    <>
      <p>
        <Link to="/console/platform/schools">← Schools</Link>
      </p>
      <PageHeader
        title={s.name}
        subtitle={
          <>
            {s.slug} · joined {formatDate(s.created_at)} {s.origin === 'signup' ? '(signed up)' : ''} · <StatusBadge status={s.status} />
          </>
        }
        actions={
          s.status !== 'pending' && (
            <button className={s.status === 'active' ? 'secondary' : ''} onClick={() => setChanging(true)}>
              {s.status === 'active' ? 'Suspend' : 'Reactivate'}
            </button>
          )
        }
      />
      {s.status === 'pending' && (
        <div className="alert info" role="status">
          This school signed up but its proprietor hasn't confirmed their email yet. Nobody can sign in until they do. Unconfirmed sign-ups
          are removed automatically after two weeks.
        </div>
      )}
      <div className="stats">
        <Card label="Pupils this term" value={String(s.pupils)} note={`${d.students_active} active students`} />
        <Card label="Bills this term" value={String(d.invoices_this_term)} note={`${formatKobo(d.collected_this_term_kobo)} collected`} />
        <Card label="Paid in, last 30 days" value={formatKobo(d.collected_30_days_kobo)} />
        <Card
          label="Owes us"
          value={formatKobo(s.subscription_owed_kobo)}
          tone={s.subscription_owed_kobo > 0 ? 'warn' : undefined}
        />
      </div>
      <div className="grid-2">
        <section className="card" aria-label="School">
          <h2>School</h2>
          <dl className="details">
            <dt>Address</dt>
            <dd>{s.address || '—'}</dd>
            <dt>Phone</dt>
            <dd>{s.phone || '—'}</dd>
            <dt>Email</dt>
            <dd>{s.email || '—'}</dd>
            <dt>Payments</dt>
            <dd>{s.payments_connected ? 'School bank account connected' : 'Not set up yet'}</dd>
            <dt>Last activity</dt>
            <dd>{d.last_activity_at ? formatDateTime(d.last_activity_at) : '—'}</dd>
          </dl>
          <label className="field">
            <span className="field-label">Plan</span>
            <select
              aria-label="Plan"
              value={d.plan_id ?? ''}
              onChange={(e) => e.target.value && setPlan.mutate(e.target.value)}
              disabled={setPlan.isPending}
            >
              {!d.plan_id && <option value="">{defaultPlan ? `${defaultPlan.name} (default)` : 'Default plan'}</option>}
              {(plans.data ?? [])
                .filter((p) => p.is_active)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
          <p className="muted small">A new plan applies to bills generated from now on.</p>
          <ErrorAlert error={setPlan.error} />
        </section>
        <section className="card" aria-label="Staff">
          <h2>Staff</h2>
          <ul className="plain">
            {d.staff.map((u) => (
              <li key={u.email} className="list-item">
                <strong>{u.full_name}</strong> <span className="muted">({ROLE_LABELS[u.role] ?? u.role})</span>
                <div className="small">
                  {u.email}
                  {' · '}
                  {u.invited ? 'invited, no password yet' : u.is_active ? 'active' : 'deactivated'}
                  {' · two-factor '}
                  {u.mfa_enabled ? 'on' : 'off'}
                </div>
                <div className="muted small">Last sign-in: {u.last_login_at ? formatDateTime(u.last_login_at) : 'never'}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <h2 className="section-title">Subscription bills</h2>
      {invoices.isPending ? <Loading /> : invoices.isError ? <ErrorAlert error={invoices.error} /> : <InvoiceTable invoices={invoices.data.items} />}
      {changing && <ChangeStatus school={s} onClose={() => setChanging(false)} />}
    </>
  );
}
