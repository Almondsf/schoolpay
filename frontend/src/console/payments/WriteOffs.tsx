import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatKobo } from '../../lib/format';
import { ErrorAlert } from '../../lib/ui';

type WriteOff = Schemas['WriteOffOut'];

/** Write-offs waiting for the proprietor. Only the proprietor sees the buttons; everyone else sees what's waiting. */
export function WriteOffsWaiting() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const waiting = useQuery({
    queryKey: ['write-offs', 'pending'],
    queryFn: async () => unwrap(await api.GET('/api/v1/write-offs', { params: { query: { status: 'pending' } } })),
  });
  const decide = useMutation({
    mutationFn: async ({ writeOff, approve, reason }: { writeOff: WriteOff; approve: boolean; reason?: string }) => {
      const path = { params: { path: { write_off_id: writeOff.id } } };
      return unwrap(
        approve
          ? await api.POST('/api/v1/write-offs/{write_off_id}/approve', path)
          : await api.POST('/api/v1/write-offs/{write_off_id}/reject', { ...path, body: { reason: reason ?? '' } }),
      );
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['write-offs'] }),
  });
  if (!waiting.data || waiting.data.length === 0) return null;
  const approves = can('debts.write_off');
  return (
    <section className="card table-card" aria-label="Write-offs waiting">
      <div className="card-pad">
        <h2>Write-offs waiting for the proprietor</h2>
        <p className="muted small">Debt the school would stop chasing. Nothing changes on the bill until the proprietor approves.</p>
      </div>
      <ErrorAlert error={decide.error} />
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Asked</th>
              <th>Pupil</th>
              <th>Bill</th>
              <th>Why</th>
              <th className="num">Amount</th>
              {approves && <th aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {waiting.data.map((w) => (
              <tr key={w.id}>
                <td className="nowrap">{formatDate(w.created_at.slice(0, 10))}</td>
                <td>
                  <Link to={`/console/students/${w.student_id}`}>{w.student_name}</Link>
                </td>
                <td>
                  <Link to={`/console/invoices/${w.invoice_id}`}>#{w.invoice_number}</Link>
                </td>
                <td className="small">{w.reason}</td>
                <td className="num">{formatKobo(w.amount_kobo)}</td>
                {approves && (
                  <td className="actions nowrap">
                    <button className="small" disabled={decide.isPending} onClick={() => decide.mutate({ writeOff: w, approve: true })}>
                      Approve
                    </button>{' '}
                    <button
                      className="secondary small"
                      disabled={decide.isPending}
                      onClick={() => {
                        const reason = window.prompt('Why is this being rejected?');
                        if (reason) decide.mutate({ writeOff: w, approve: false, reason });
                      }}
                    >
                      Reject
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
