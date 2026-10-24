import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatKobo, lagosToday, parseNairaToKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, Stat, Tabs, fieldError } from '../../lib/ui';

type Expense = Schemas['ExpenseOut'];
type Status = Schemas['ExpenseStatus'];
type PaidFrom = Schemas['PaidFrom'];
const PAGE = 50;

const PAID_FROM: Record<PaidFrom, string> = { cash: 'Cash from the drawer', bank: 'Bank transfer or cheque', card: 'Card or POS' };
const STATUS_LABEL: Record<Status, string> = { pending: 'Awaiting approval', approved: 'Approved', rejected: 'Rejected', voided: 'Voided' };
const STATUS_TONE: Record<Status, string> = { pending: 'warn', approved: 'ok', rejected: 'danger', voided: '' };

function daysBefore(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** What the school spends, approved by a second person, and money in against money out. */
export function Expenses() {
  const { can } = useAuth();
  const [tab, setTab] = useState<'spending' | 'summary'>('spending');
  const [adding, setAdding] = useState(false);
  return (
    <>
      <PageHeader
        title="Expenses"
        subtitle="What the school spends, approved by a second person, against the money coming in."
        actions={can('payments.record') && <button onClick={() => setAdding(true)}>Record spending</button>}
      />
      <Tabs
        label="Show"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: 'spending', label: 'Spending' },
          ...(can('reports.view') ? [{ key: 'summary' as const, label: 'Income and expenditure' }] : []),
        ]}
      />
      {tab === 'spending' ? <Spending /> : <IncomeAndExpenditure />}
      {adding && <RecordSpending onClose={() => setAdding(false)} />}
    </>
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ['expenses', 'income-expenditure', 'cash-book']) void queryClient.invalidateQueries({ queryKey: [key] });
  };
}

function Spending() {
  const { can } = useAuth();
  const refresh = useRefresh();
  const [status, setStatus] = useState<Status | ''>('');
  const [offset, setOffset] = useState(0);
  const [deciding, setDeciding] = useState<{ expense: Expense; action: 'reject' | 'void' } | null>(null);
  const list = useQuery({
    queryKey: ['expenses', status, offset],
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/expenses', { params: { query: { status: status || undefined, limit: PAGE, offset } } })),
  });
  const approve = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST('/api/v1/expenses/{expense_id}/approve', { params: { path: { expense_id: id } } })),
    onSuccess: refresh,
  });
  return (
    <>
      <div className="row wrap">
        <Field label="Show">
          <select
            id="expense-status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as Status | '');
              setOffset(0);
            }}
          >
            <option value="">All spending</option>
            <option value="pending">Awaiting approval</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="voided">Voided</option>
          </select>
        </Field>
      </div>
      <ErrorAlert error={approve.error} />
      {list.isPending ? (
        <Loading />
      ) : list.isError ? (
        <ErrorAlert error={list.error} />
      ) : list.data.items.length === 0 ? (
        <Empty>No spending recorded yet.</Empty>
      ) : (
        <div className="card table-card">
          <table aria-label="Spending">
            <thead>
              <tr>
                <th>Date</th>
                <th>Paid to</th>
                <th>Category</th>
                <th>From</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((e) => (
                <tr key={e.id} className={e.status === 'voided' || e.status === 'rejected' ? 'retired' : ''}>
                  <td className="nowrap small">{formatDate(e.spent_on)}</td>
                  <td>
                    {e.payee}
                    {(e.description || e.reference) && (
                      <div className="muted small">{[e.description, e.reference && `Ref ${e.reference}`].filter(Boolean).join(' · ')}</div>
                    )}
                    {(e.rejection_reason || e.void_reason) && <div className="small">{e.rejection_reason || e.void_reason}</div>}
                  </td>
                  <td className="small">{e.category}</td>
                  <td className="small">{PAID_FROM[e.paid_from]}</td>
                  <td className="num">{formatKobo(e.amount_kobo)}</td>
                  <td>
                    <span className={`badge ${STATUS_TONE[e.status]}`}>{STATUS_LABEL[e.status]}</span>
                  </td>
                  <td className="actions nowrap">
                    {can('payments.approve') && e.status === 'pending' && (
                      <>
                        <button className="small" onClick={() => approve.mutate(e.id)} disabled={approve.isPending}>
                          Approve
                        </button>{' '}
                        <button className="link small" onClick={() => setDeciding({ expense: e, action: 'reject' })}>
                          Reject
                        </button>
                      </>
                    )}
                    {can('payments.approve') && e.status === 'approved' && (
                      <button className="link small" onClick={() => setDeciding({ expense: e, action: 'void' })}>
                        Void
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={list.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {deciding && <Decide {...deciding} onClose={() => setDeciding(null)} />}
    </>
  );
}

function Decide({ expense, action, onClose }: { expense: Expense; action: 'reject' | 'void'; onClose: () => void }) {
  const refresh = useRefresh();
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        action === 'reject'
          ? await api.POST('/api/v1/expenses/{expense_id}/reject', { params: { path: { expense_id: expense.id } }, body: { reason } })
          : await api.POST('/api/v1/expenses/{expense_id}/void', { params: { path: { expense_id: expense.id } }, body: { reason } }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title={action === 'reject' ? 'Reject spending' : 'Void spending'} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>
          {formatKobo(expense.amount_kobo)} to {expense.payee} on {formatDate(expense.spent_on)}.{' '}
          {action === 'void' ? 'It stops counting but stays on record.' : "It won't count."}
        </p>
        <ErrorAlert error={save.error} />
        <Field label="Reason">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} maxLength={300} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="danger" disabled={save.isPending}>
            {action === 'reject' ? 'Reject' : 'Void'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function RecordSpending({ onClose }: { onClose: () => void }) {
  const refresh = useRefresh();
  const categories = useQuery({
    queryKey: ['expense-categories'],
    queryFn: async () => unwrap(await api.GET('/api/v1/expense-categories')),
  });
  const [form, setForm] = useState({
    category_id: '',
    amount: '',
    spent_on: lagosToday(),
    payee: '',
    description: '',
    paid_from: 'cash' as PaidFrom,
    reference: '',
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const kobo = parseNairaToKobo(form.amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/expenses', {
          body: {
            category_id: form.category_id,
            amount_kobo: kobo ?? 0,
            spent_on: form.spent_on,
            payee: form.payee,
            description: form.description,
            paid_from: form.paid_from,
            reference: form.reference,
          },
        }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title="Record spending" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>Someone else approves it before it counts.</p>
        <ErrorAlert
          error={['amount_kobo', 'spent_on', 'payee', 'category_id'].some((f) => fieldError(save.error, f)) ? null : save.error}
        />
        <Field label="Category" error={fieldError(save.error, 'category_id')}>
          <select value={form.category_id} onChange={set('category_id')} required>
            <option value="">Choose…</option>
            {categories.data
              ?.filter((c) => c.is_active)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </Field>
        <Field
          label="Amount (₦)"
          error={fieldError(save.error, 'amount_kobo') ?? (form.amount && kobo === null ? 'Not a valid amount' : undefined)}
        >
          <input value={form.amount} onChange={set('amount')} inputMode="decimal" required />
        </Field>
        <Field label="Paid to" error={fieldError(save.error, 'payee')}>
          <input
            value={form.payee}
            onChange={set('payee')}
            required
            minLength={2}
            maxLength={150}
            placeholder="e.g. Total filling station"
          />
        </Field>
        <Field label="What for (optional)">
          <input value={form.description} onChange={set('description')} maxLength={300} placeholder="e.g. Generator diesel, 200 litres" />
        </Field>
        <Field label="Spent on" error={fieldError(save.error, 'spent_on')}>
          <input type="date" value={form.spent_on} max={lagosToday()} onChange={set('spent_on')} required />
        </Field>
        <Field label="Paid from" hint={form.paid_from === 'cash' ? 'It comes off the cash that should be in the drawer.' : undefined}>
          <select value={form.paid_from} onChange={set('paid_from')}>
            {(Object.keys(PAID_FROM) as PaidFrom[]).map((p) => (
              <option key={p} value={p}>
                {PAID_FROM[p]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Receipt or reference number (optional)">
          <input value={form.reference} onChange={set('reference')} maxLength={100} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!kobo || !form.category_id || save.isPending}>
            Record spending
          </button>
        </div>
      </form>
    </Modal>
  );
}

function IncomeAndExpenditure() {
  const today = lagosToday();
  const [start, setStart] = useState(daysBefore(today, 29));
  const [end, setEnd] = useState(today);
  const report = useQuery({
    queryKey: ['income-expenditure', start, end],
    queryFn: async () => unwrap(await api.GET('/api/v1/reports/income-expenditure', { params: { query: { start, end } } })),
  });
  return (
    <>
      <div className="row wrap">
        <Field label="From">
          <input id="ie-from" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="To">
          <input id="ie-to" type="date" value={end} min={start} max={today} onChange={(e) => setEnd(e.target.value)} />
        </Field>
      </div>
      {report.isPending ? (
        <Loading />
      ) : report.isError ? (
        <ErrorAlert error={report.error} />
      ) : (
        <>
          <div className="stats">
            <Stat label="Money in" value={formatKobo(report.data.income_kobo)} tone="ok" />
            <Stat label="Money out" value={formatKobo(report.data.expenditure_kobo)} />
            <Stat
              label={report.data.surplus_kobo >= 0 ? 'Surplus' : 'Deficit'}
              value={formatKobo(Math.abs(report.data.surplus_kobo))}
              tone={report.data.surplus_kobo >= 0 ? 'ok' : 'danger'}
              note={
                report.data.awaiting_approval_kobo > 0
                  ? `${formatKobo(report.data.awaiting_approval_kobo)} more spending awaits approval`
                  : undefined
              }
            />
          </div>
          <div className="grid-2">
            <section className="card" aria-label="Money in">
              <h2>Money in</h2>
              <LineTable lines={report.data.income} total={report.data.income_kobo} empty="No payments in these dates." />
              <p className="muted small">Bills issued in these dates came to {formatKobo(report.data.billed_kobo)}.</p>
            </section>
            <section className="card" aria-label="Money out">
              <h2>Money out</h2>
              <LineTable
                lines={report.data.expenditure}
                total={report.data.expenditure_kobo}
                empty="No approved spending in these dates."
              />
            </section>
          </div>
        </>
      )}
    </>
  );
}

function LineTable({ lines, total, empty }: { lines: Schemas['LineOut'][]; total: number; empty: string }) {
  if (lines.length === 0) return <p className="muted">{empty}</p>;
  return (
    <table>
      <tbody>
        {lines.map((l) => (
          <tr key={l.label}>
            <td>
              {l.label} <span className="muted small">({l.count})</span>
            </td>
            <td className="num">{formatKobo(l.amount_kobo)}</td>
          </tr>
        ))}
        <tr>
          <td>
            <strong>Total</strong>
          </td>
          <td className="num">
            <strong>{formatKobo(total)}</strong>
          </td>
        </tr>
      </tbody>
    </table>
  );
}
