import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ellipsis } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatDateTime, formatKobo, humanize, koboToInput, lagosToday, parseNairaToKobo } from '../../lib/format';
import { METHOD_LABELS } from '../Dashboard';
import { Empty, ErrorAlert, Field, Loading, Menu, Modal, Notice, PageHeader, Pager, Tabs } from '../../lib/ui';
import { ReceiptLink } from './ReceiptLink';
import { StudentPicker, type PickedStudent } from './StudentPicker';
import { FeePicker, leftOn } from '../../lib/FeeBreakdown';

type Payment = Schemas['PaymentOut'];
type Manual = Schemas['ManualOut'];
const PAGE = 50;
const MANUAL_METHODS = ['cash', 'teller', 'pos', 'bank_transfer'] as const;

export function Payments() {
  const { can } = useAuth();
  const [tab, setTab] = useState<'received' | 'pending'>('received');
  const [recording, setRecording] = useState(false);
  // The same query as the approvals tab, so its count costs nothing extra
  const waiting = useQuery({
    queryKey: ['manual-payments', 'pending'],
    enabled: can('payments.view'),
    queryFn: async () => unwrap(await api.GET('/api/v1/manual-payments', { params: { query: { status: 'pending' } } })),
  });
  const count = waiting.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Payments"
        subtitle="Transfers into students' account numbers appear here automatically. Cash and tellers need a second person to approve."
        actions={can('payments.record') && <button onClick={() => setRecording(true)}>Record cash or teller</button>}
      />
      <Tabs
        tabs={[
          { key: 'received', label: 'Received' },
          {
            key: 'pending',
            label: (
              <>
                Awaiting approval{count > 0 && <span className="tab-count" aria-hidden="true">{count}</span>}
              </>
            ),
          },
        ]}
        value={tab}
        onChange={setTab}
      />
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
  const [marking, setMarking] = useState<Payment | null>(null);
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
                <th>Pupil</th>
                <th className="num">Amount</th>
                <th>On bills</th>
                {(can('payments.reverse') || can('payments.record')) && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {payments.data.items.map((p) => (
                <tr key={p.id} className={p.reversed || p.kind === 'reversal' ? 'retired' : ''}>
                  <td className="nowrap small">{formatDateTime(p.received_at)}</td>
                  <td>
                    <Link to={`/console/students/${p.student.id}`}>{p.student.full_name}</Link>
                    <div className="muted small">{p.student.admission_number}</div>
                  </td>
                  <td className="num">
                    <strong>{formatKobo(p.amount_kobo)}</strong>
                    <div className="muted small" title={p.reference || p.gateway_reference || undefined}>
                      {p.kind === 'reversal' ? 'Reversal' : METHOD_LABELS[p.method]}
                    </div>
                    {p.for_fees.length > 0 && <div className="small">For {p.for_fees.join(', ')}</div>}
                    {p.reversed && <span className="badge danger">Reversed</span>}
                  </td>
                  <td className="small">
                    {p.applied.map((a) => (
                      <div key={a.invoice_id}>
                        <Link to={`/console/invoices/${a.invoice_id}`}>#{a.invoice_number}</Link> {formatKobo(a.amount_kobo)}
                      </div>
                    ))}
                    {p.unapplied_kobo !== 0 && <div className="muted">{formatKobo(p.unapplied_kobo)} as credit</div>}
                    {p.receipt_id && p.receipt_number && <ReceiptLink receiptId={p.receipt_id} number={p.receipt_number} />}
                  </td>
                  {(can('payments.reverse') || can('payments.record')) && (
                    <td className="actions">
                      {p.kind === 'receipt' && !p.reversed && (
                        <Menu label={`Actions for ${formatKobo(p.amount_kobo)} from ${p.student.full_name}`} icon={<Ellipsis size={18} />}>
                          {can('payments.record') && p.for_fees.length === 0 && p.applied.length > 0 && (
                            <button onClick={() => setMarking(p)}>Mark for fees</button>
                          )}
                          {can('payments.reverse') && (
                            <button className="danger-item" onClick={() => setReversing(p)}>
                              Reverse
                            </button>
                          )}
                        </Menu>
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
      {marking && <MarkForFees payment={marking} onClose={() => setMarking(null)} />}
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
                    {m.for_fees.length > 0 && <div className="small">For: {m.for_fees.join(', ')}</div>}
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
    received_on: lagosToday(),
    reference: '',
    note: '',
  });
  const kobo = parseNairaToKobo(form.amount);
  const [picked, setPicked] = useState<string[]>([]);
  const breakdown = useQuery({
    queryKey: ['account', student?.id ?? '', 'fee-breakdown'],
    enabled: !!student,
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/reports/students/{student_id}/fee-breakdown', { params: { path: { student_id: student!.id } } })),
  });
  const pick = (names: string[]) => {
    setPicked(names);
    const left = leftOn(breakdown.data?.fees ?? [], names);
    if (names.length > 0) setForm({ ...form, amount: koboToInput(left) }); // the whole of those fees; can be lowered
  };
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/manual-payments', {
          body: {
            fees: picked,
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
          <StudentPicker
            value={student}
            onChange={(s) => {
              setStudent(s);
              setPicked([]);
            }}
          />
        </Field>
        {breakdown.data && <FeePicker fees={breakdown.data.fees} picked={picked} onChange={pick} />}
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

/**
 * Say what a payment was for after it arrived (a parent says their transfer was for the PTA levy).
 * Only which fees show as paid changes; marks are permanent, so a payment is marked once.
 */
function MarkForFees({ payment, onClose }: { payment: Payment; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<string[]>([]);
  const breakdown = useQuery({
    queryKey: ['account', payment.student.id, 'fee-breakdown'],
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/reports/students/{student_id}/fee-breakdown', { params: { path: { student_id: payment.student.id } } })),
  });
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/payments/{payment_id}/fees', { params: { path: { payment_id: payment.id } }, body: { fees: picked } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['payments'] });
      void queryClient.invalidateQueries({ queryKey: ['account', payment.student.id] });
      onClose();
    },
  });
  const fees = (breakdown.data?.fees ?? []).filter((f) => f.billed_kobo > 0);
  return (
    <Modal title={`What was ${formatKobo(payment.amount_kobo)} from ${payment.student.full_name} for?`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted">
          Tick the fees the parent says this payment was for. Those fees then show it as paid; what's owed in total doesn't change. This can only
          be done once for each payment.
        </p>
        <ErrorAlert error={breakdown.error ?? save.error} />
        {breakdown.isPending ? (
          <Loading />
        ) : (
          <fieldset className="fee-picker">
            <legend>Fees on this term's bill</legend>
            {fees.map((f) => (
              <label key={f.name} className="checkbox">
                <input
                  type="checkbox"
                  checked={picked.includes(f.name)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, f.name] : picked.filter((n) => n !== f.name))}
                />
                {f.name}{' '}
                <span className="muted small">
                  {formatKobo(f.billed_kobo)} · {f.left_kobo > 0 ? `${formatKobo(f.left_kobo)} left` : 'paid'}
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={picked.length === 0 || save.isPending}>
            Mark for {picked.length === 1 ? picked[0] : `${picked.length || ''} fees`.trim()}
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
