import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatDateTime, formatKobo, humanize, parseNairaToKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, Notice, PageHeader, Pager } from '../../lib/ui';
import { StudentPicker, type PickedStudent } from './StudentPicker';

type Payment = Schemas['PaymentOut'];
type Manual = Schemas['ManualOut'];
const PAGE = 50;
const MANUAL_METHODS = ['cash', 'teller', 'pos', 'bank_transfer'] as const;

export function Payments() {
  const { can } = useAuth();
  const [tab, setTab] = useState<'received' | 'pending'>('received');
  const [recording, setRecording] = useState(false);
  return (
    <>
      <PageHeader
        title="Payments"
        subtitle="Transfers into students' account numbers appear here automatically. Cash and tellers need a second person to approve."
        actions={can('payments.record') && <button onClick={() => setRecording(true)}>Record cash or teller</button>}
      />
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'received'} className={tab === 'received' ? 'tab active' : 'tab'} onClick={() => setTab('received')}>
          Received
        </button>
        <button role="tab" aria-selected={tab === 'pending'} className={tab === 'pending' ? 'tab active' : 'tab'} onClick={() => setTab('pending')}>
          Awaiting approval
        </button>
      </div>
      {tab === 'received' ? <Received /> : <Pending />}
      {recording && <RecordManual onClose={() => setRecording(false)} onDone={() => setTab('pending')} />}
    </>
  );
}

function Received() {
  const { can } = useAuth();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [reversing, setReversing] = useState<Payment | null>(null);
  const payments = useQuery({
    queryKey: ['payments', search, offset],
    queryFn: async () => unwrap(await api.GET('/api/v1/payments', { params: { query: { search: search || undefined, limit: PAGE, offset } } })),
  });
  return (
    <>
      <div className="filters">
        <input
          type="search"
          placeholder="Student, admission number or reference"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </div>
      {payments.isPending ? (
        <Loading />
      ) : payments.isError ? (
        <ErrorAlert error={payments.error} />
      ) : payments.data.items.length === 0 ? (
        <Empty>No payments yet.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Received</th>
                <th>Student</th>
                <th>How</th>
                <th className="num">Amount</th>
                <th>Applied to</th>
                {can('payments.reverse') && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {payments.data.items.map((p) => (
                <tr key={p.id} className={p.reversed || p.kind === 'reversal' ? 'retired' : ''}>
                  <td className="nowrap">{formatDateTime(p.received_at)}</td>
                  <td>
                    <Link to={`/console/students/${p.student.id}`}>{p.student.full_name}</Link>
                    <div className="muted small">{p.student.admission_number}</div>
                  </td>
                  <td>
                    {p.kind === 'reversal' ? 'Reversal' : humanize(p.method)}
                    {(p.reference || p.gateway_reference) && <div className="muted small">{p.reference || p.gateway_reference}</div>}
                    {p.reversed && <span className="badge danger">Reversed</span>}
                  </td>
                  <td className="num">{formatKobo(p.amount_kobo)}</td>
                  <td className="small">
                    {p.applied.map((a) => (
                      <div key={a.invoice_id}>
                        <Link to={`/console/invoices/${a.invoice_id}`}>#{a.invoice_number}</Link> {formatKobo(a.amount_kobo)}
                      </div>
                    ))}
                    {p.unapplied_kobo !== 0 && <div className="muted">{formatKobo(p.unapplied_kobo)} as credit</div>}
                  </td>
                  {can('payments.reverse') && (
                    <td className="actions">
                      {p.kind === 'receipt' && !p.reversed && (
                        <button className="secondary small" onClick={() => setReversing(p)}>
                          Reverse
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={payments.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {reversing && <Reverse payment={reversing} onClose={() => setReversing(null)} />}
    </>
  );
}

function Pending() {
  const { can, user } = useAuth();
  const queryClient = useQueryClient();
  const pending = useQuery({
    queryKey: ['manual-payments', 'pending'],
    queryFn: async () => unwrap(await api.GET('/api/v1/manual-payments', { params: { query: { status: 'pending' } } })),
  });
  const decide = useMutation({
    mutationFn: async ({ entry, approve, reason }: { entry: Manual; approve: boolean; reason?: string }) => {
      const path = { params: { path: { entry_id: entry.id } } };
      return unwrap(
        approve
          ? await api.POST('/api/v1/manual-payments/{entry_id}/approve', path)
          : await api.POST('/api/v1/manual-payments/{entry_id}/reject', { ...path, body: { reason: reason ?? '' } }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['manual-payments'] });
      void queryClient.invalidateQueries({ queryKey: ['payments'] });
    },
  });

  if (pending.isPending) return <Loading />;
  if (pending.isError) return <ErrorAlert error={pending.error} />;
  if (pending.data.items.length === 0) return <Empty>Nothing waiting for approval.</Empty>;
  return (
    <>
      <ErrorAlert error={decide.error} />
      <div className="card table-card">
        <table>
          <thead>
            <tr>
              <th>Received</th>
              <th>Student</th>
              <th>How</th>
              <th className="num">Amount</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {pending.data.items.map((m) => {
              const mine = m.recorded_by === user?.id;
              return (
                <tr key={m.id}>
                  <td>{formatDate(m.received_on)}</td>
                  <td>
                    {m.student.full_name} <span className="muted small">({m.student.admission_number})</span>
                    {m.note && <div className="muted small">{m.note}</div>}
                  </td>
                  <td>
                    {humanize(m.method)}
                    {m.reference && <div className="muted small">{m.reference}</div>}
                  </td>
                  <td className="num">{formatKobo(m.amount_kobo)}</td>
                  <td className="actions nowrap">
                    {can('payments.approve') &&
                      (mine ? (
                        <span className="muted small">Recorded by you: someone else must approve</span>
                      ) : (
                        <>
                          <button className="small" onClick={() => decide.mutate({ entry: m, approve: true })} disabled={decide.isPending}>
                            Approve
                          </button>{' '}
                          <button
                            className="secondary small"
                            onClick={() => {
                              const reason = window.prompt('Why is this being rejected?');
                              if (reason && reason.trim().length >= 3) decide.mutate({ entry: m, approve: false, reason });
                            }}
                          >
                            Reject
                          </button>
                        </>
                      ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RecordManual({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [student, setStudent] = useState<PickedStudent | null>(null);
  const [form, setForm] = useState({
    amount: '',
    method: 'cash' as (typeof MANUAL_METHODS)[number],
    received_on: new Date().toISOString().slice(0, 10),
    reference: '',
    note: '',
  });
  const kobo = parseNairaToKobo(form.amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/manual-payments', {
          body: {
            student_id: student!.id,
            amount_kobo: kobo!,
            method: form.method,
            received_on: form.received_on,
            reference: form.reference,
            note: form.note,
          },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['manual-payments'] });
      onDone();
      onClose();
    },
  });
  return (
    <Modal title="Record cash or teller" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Notice>It won't count until someone else approves it.</Notice>
        <ErrorAlert error={save.error} />
        <Field label="Student">
          <StudentPicker value={student} onChange={setStudent} />
        </Field>
        <div className="grid-2 tight">
          <Field label="Amount (₦)" error={form.amount && kobo === null ? 'Not a valid amount' : undefined}>
            <input value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} inputMode="decimal" required />
          </Field>
          <Field label="How it was paid">
            <select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as (typeof MANUAL_METHODS)[number] })}>
              {MANUAL_METHODS.map((m) => (
                <option key={m} value={m}>
                  {humanize(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Date received">
            <input type="date" value={form.received_on} onChange={(e) => setForm({ ...form, received_on: e.target.value })} required />
          </Field>
          <Field label={form.method === 'teller' ? 'Teller number' : 'Reference (optional)'}>
            <input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} required={form.method === 'teller'} />
          </Field>
        </div>
        <Field label="Note (optional)">
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!student || !kobo || save.isPending}>
            Record
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Reverse({ payment, onClose }: { payment: Payment; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/payments/{payment_id}/reverse', { params: { path: { payment_id: payment.id } }, body: { reason } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['payments'] });
      void queryClient.invalidateQueries({ queryKey: ['account', payment.student.id] });
      onClose();
    },
  });
  return (
    <Modal title={`Reverse ${formatKobo(payment.amount_kobo)} from ${payment.student.full_name}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>For a payment recorded in error, e.g. a bounced teller. The payment stays on record with a matching reversal, and what it paid becomes owed again.</p>
        <ErrorAlert error={save.error} />
        <Field label="Reason">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="danger" disabled={save.isPending}>
            Reverse payment
          </button>
        </div>
      </form>
    </Modal>
  );
}
