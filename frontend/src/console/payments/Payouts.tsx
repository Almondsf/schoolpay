import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatDateTime, formatKobo, lagosToday } from '../../lib/format';
import { Empty, ErrorAlert, Loading } from '../../lib/ui';
import { METHOD_LABELS } from '../Dashboard';

/** What the payment provider paid into the school's bank, matched to the payments it covered. */
export function Payouts() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const today = lagosToday();
  const monthAgo = new Date(Date.parse(`${today}T12:00:00Z`) - 29 * 86_400_000).toISOString().slice(0, 10);
  const [start, setStart] = useState(monthAgo);
  const [end, setEnd] = useState(today);
  const period = { start, end };
  const report = useQuery({
    queryKey: ['settlements', period],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/settlements', { params: { query: period } })),
    enabled: Boolean(start && end),
  });
  const refresh = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/settlements/refresh')),
    onSuccess: () => {
      // The check runs in the background; look again shortly
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['settlements'] }), 3000);
    },
  });
  if (report.isPending) return <Loading />;
  if (report.isError) return <ErrorAlert error={report.error} />;
  const data = report.data;
  if (!data.connected) {
    return <Empty>Payouts appear here once the school is set up to take payments through Paystack.</Empty>;
  }
  return (
    <>
      <p className="muted small">
        Paystack pays parents’ transfers and card payments into the school’s bank, usually the next working day, less its charges. Each
        payout is matched to the payments it covered.{' '}
        {data.checked_at ? `Last checked ${formatDateTime(data.checked_at)}.` : 'Not checked yet.'}
      </p>
      <div className="filters">
        <label className="inline-field">
          From <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="inline-field">
          To <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </label>
        {can('payments.approve') && (
          <button className="secondary small" onClick={() => refresh.mutate()} disabled={refresh.isPending || refresh.isSuccess}>
            {refresh.isSuccess ? 'Checking…' : 'Check now'}
          </button>
        )}
      </div>
      <ErrorAlert error={refresh.error} />
      <div className="stats compact">
        <div>
          <div className="muted small">Parents paid</div>
          <div className="stat-value">{formatKobo(data.gross_kobo)}</div>
        </div>
        <div>
          <div className="muted small">Charges</div>
          <div className="stat-value">{formatKobo(data.charges_kobo)}</div>
        </div>
        <div>
          <div className="muted small">Paid into the bank</div>
          <div className="stat-value">{formatKobo(data.paid_out_kobo)}</div>
        </div>
      </div>

      {data.unsettled.length > 0 && (
        <section className="card table-card" aria-label="Not paid out yet">
          <div className="card-pad">
            <h2>Not paid out yet</h2>
            <p className="muted small">
              {formatKobo(data.unsettled_kobo)} received more than 3 days ago hasn’t been in a payout. Check with Paystack if it stays here.
            </p>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Received</th>
                  <th>Receipt</th>
                  <th>Pupil</th>
                  <th>How</th>
                  <th>Reference</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {data.unsettled.map((u) => (
                  <tr key={u.payment_id}>
                    <td className="nowrap">{formatDate(u.received_at.slice(0, 10))}</td>
                    <td>{u.receipt_number ?? ''}</td>
                    <td>{u.admission_number}</td>
                    <td>{METHOD_LABELS[u.method]}</td>
                    <td className="small">{u.reference}</td>
                    <td className="num">{formatKobo(u.amount_kobo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {data.unknown.length > 0 && (
        <section className="card table-card" aria-label="Paid out but not in WardPay">
          <div className="card-pad">
            <h2>Paid out, but not in WardPay</h2>
            <p className="muted small">Paystack paid these to the school, but WardPay has no payment with that reference.</p>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Payout</th>
                  <th>Reference</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {data.unknown.map((u) => (
                  <tr key={`${u.settled_on}-${u.reference}`}>
                    <td className="nowrap">{formatDate(u.settled_on)}</td>
                    <td className="small">{u.reference}</td>
                    <td className="num">{formatKobo(u.amount_kobo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {data.payouts.length === 0 ? (
        <Empty>
          No payouts between {formatDate(start)} and {formatDate(end)}.
        </Empty>
      ) : (
        <section className="card table-card" aria-label="Payouts">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Paid on</th>
                  <th>Status</th>
                  <th className="num">Parents paid</th>
                  <th className="num">Charges</th>
                  <th className="num">Into the bank</th>
                  <th className="num">Payments matched</th>
                </tr>
              </thead>
              <tbody>
                {data.payouts.map((p) => (
                  <tr key={p.id}>
                    <td className="nowrap">{formatDate(p.settled_on)}</td>
                    <td>
                      <span className={p.status === 'success' ? 'badge ok' : 'badge'}>
                        {p.status === 'success' ? 'Paid' : 'On its way'}
                      </span>
                    </td>
                    <td className="num">{formatKobo(p.gross_kobo)}</td>
                    <td className="num">{formatKobo(p.charges_kobo)}</td>
                    <td className="num">{formatKobo(p.paid_out_kobo)}</td>
                    <td className={p.matched < p.transactions ? 'num warn-text' : 'num'}>
                      {p.matched} of {p.transactions}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
