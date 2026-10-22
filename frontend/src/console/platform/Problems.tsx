import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap } from '../../api/client';
import { formatDateTime, formatKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader } from '../../lib/ui';

type Resolving = { kind: 'webhook'; id: string; label: string } | { kind: 'checkout'; reference: string; label: string };

/** Everything stuck across schools that needs a person: parked payments, payments in review, failed tasks and messages. */
export function PlatformProblems() {
  const queryClient = useQueryClient();
  const problems = useQuery({
    queryKey: ['platform-problems'],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/problems')),
    staleTime: 0,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform-problems'] });
    void queryClient.invalidateQueries({ queryKey: ['platform-overview'] });
  };
  const retryWebhook = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST('/api/v1/platform/problems/webhooks/{event_id}/retry', { params: { path: { event_id: id } } })),
    onSuccess: refresh,
  });
  const retryTask = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST('/api/v1/platform/problems/tasks/{task_id}/retry', { params: { path: { task_id: id } } })),
    onSuccess: refresh,
  });
  const [resolving, setResolving] = useState<Resolving | null>(null);

  if (problems.isPending) return <Loading />;
  if (problems.isError) return <ErrorAlert error={problems.error} />;
  const { webhooks, checkouts, tasks, messages } = problems.data;
  const nothing = webhooks.length + checkouts.length + tasks.length === 0;
  return (
    <>
      <PageHeader title="Problems to fix" subtitle="Payments and tasks that got stuck, across every school." />
      <ErrorAlert error={retryWebhook.error ?? retryTask.error} />
      {nothing && <Empty>Nothing is stuck. Payments, notifications and background tasks are all going through.</Empty>}

      {webhooks.length > 0 && (
        <section aria-label="Payment notifications">
          <h2 className="section-title">Payment notifications set aside ({webhooks.length})</h2>
          <p className="muted small">
            Money a provider told us about that couldn't be matched safely, or that kept failing. Fix the cause (for example, the account
            number), then retry; or resolve it with a note if it was handled by hand.
          </p>
          <div className="card table-card">
            <table>
              <thead>
                <tr>
                  <th>Received</th>
                  <th>School</th>
                  <th className="num">Amount</th>
                  <th>Why</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {webhooks.map((w) => (
                  <tr key={w.id}>
                    <td className="nowrap small">{formatDateTime(w.received_at)}</td>
                    <td>{w.school_name ?? <span className="muted">Unknown</span>}</td>
                    <td className="num">{w.claimed_amount_kobo != null ? formatKobo(w.claimed_amount_kobo) : '—'}</td>
                    <td className="small">
                      <span className={`badge ${w.status === 'failed' ? 'danger' : ''}`}>
                        {w.status === 'failed' ? 'Failed' : 'Needs review'}
                      </span>{' '}
                      {w.note || w.event_type}
                      <div className="muted">
                        {w.gateway} · {w.reference ?? 'no reference'} · {w.attempts} attempts
                      </div>
                    </td>
                    <td className="nowrap">
                      <button className="small secondary" onClick={() => retryWebhook.mutate(w.id)} disabled={retryWebhook.isPending}>
                        Retry
                      </button>{' '}
                      <button
                        className="small secondary"
                        onClick={() => setResolving({ kind: 'webhook', id: w.id, label: w.reference ?? w.id })}
                      >
                        Resolve
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {checkouts.length > 0 && (
        <section aria-label="Card payments in review">
          <h2 className="section-title">Card payments in review ({checkouts.length})</h2>
          <p className="muted small">
            Paid, but not what was expected, so nothing was recorded. Sort it out with the school, then note what you did.
          </p>
          <div className="card table-card">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>School</th>
                  <th>For</th>
                  <th className="num">Expected</th>
                  <th>Why</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {checkouts.map((c) => (
                  <tr key={c.reference}>
                    <td className="nowrap small">{formatDateTime(c.created_at)}</td>
                    <td>{c.school_name}</td>
                    <td className="small">{c.kind === 'subscription' ? 'Subscription bill' : 'School fees'}</td>
                    <td className="num">{formatKobo(c.amount_kobo)}</td>
                    <td className="small">
                      {c.review_reason}
                      <div className="muted">{c.reference}</div>
                    </td>
                    <td>
                      <button
                        className="small secondary"
                        onClick={() => setResolving({ kind: 'checkout', reference: c.reference, label: c.reference })}
                      >
                        Resolve
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tasks.length > 0 && (
        <section aria-label="Background tasks">
          <h2 className="section-title">Background tasks that gave up ({tasks.length})</h2>
          <div className="card table-card">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Task</th>
                  <th>Last error</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id}>
                    <td className="nowrap small">{formatDateTime(t.created_at)}</td>
                    <td className="small">{t.task_name}</td>
                    <td className="small">
                      {t.last_error || '—'} <span className="muted">({t.attempts} attempts)</span>
                    </td>
                    <td>
                      <button className="small secondary" onClick={() => retryTask.mutate(t.id)} disabled={retryTask.isPending}>
                        Retry
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section aria-label="Failed messages">
        <h2 className="section-title">Messages that failed (last 7 days)</h2>
        {messages.length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <div className="card table-card">
            <table>
              <thead>
                <tr>
                  <th>School</th>
                  <th className="num">Failed</th>
                  <th>Example error</th>
                </tr>
              </thead>
              <tbody>
                {messages.map((m) => (
                  <tr key={m.school_name}>
                    <td>{m.school_name}</td>
                    <td className="num">{m.failed}</td>
                    <td className="small">{m.last_error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="hint">Many failures at once usually mean a messaging provider is down or out of credit: check Providers.</p>
      </section>

      {resolving && (
        <ResolveDialog
          resolving={resolving}
          onClose={() => setResolving(null)}
          onDone={() => {
            setResolving(null);
            refresh();
          }}
        />
      )}
    </>
  );
}

function ResolveDialog({ resolving, onClose, onDone }: { resolving: Resolving; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState('');
  const resolve = useMutation({
    mutationFn: async () =>
      resolving.kind === 'webhook'
        ? unwrap(
            await api.POST('/api/v1/platform/problems/webhooks/{event_id}/resolve', {
              params: { path: { event_id: resolving.id } },
              body: { note },
            }),
          )
        : unwrap(
            await api.POST('/api/v1/platform/problems/checkouts/{reference}/resolve', {
              params: { path: { reference: resolving.reference } },
              body: { note },
            }),
          ),
    onSuccess: onDone,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    resolve.mutate();
  };
  return (
    <Modal title="Resolve" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="small">
          <strong>{resolving.label}</strong>: say what was done, for the record. Nothing about the money changes here.
        </p>
        <Field label="What was done">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Refunded by Paystack on 23 Oct"
            minLength={3}
            required
          />
        </Field>
        <ErrorAlert error={resolve.error} />
        <div className="row">
          <button type="submit" disabled={resolve.isPending}>
            {resolve.isPending ? 'Saving…' : 'Resolve'}
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
