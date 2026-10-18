import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { INVOICE_KIND_LABELS, formatKobo, parseNairaToKobo } from '../../lib/format';
import { ErrorAlert, Field, Loading, Modal, StatusBadge } from '../../lib/ui';
import { describeDiscount } from '../fees/FeeSetup';

/** A student's fees: balance across all invoices (arrears included), each invoice, and their discounts. */
export function StudentAccount({ studentId }: { studentId: string }) {
  const { can } = useAuth();
  const issue = can('invoices.issue');
  const manageFees = can('fees.manage');
  const queryClient = useQueryClient();
  const path = { params: { path: { student_id: studentId } } };
  const account = useQuery({ queryKey: ['account', studentId], queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}/account', path)) });
  const given = useQuery({ queryKey: ['student-discounts', studentId], queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}/discounts', path)) });
  const discounts = useQuery({ queryKey: ['discounts'], enabled: manageFees, queryFn: async () => unwrap(await api.GET('/api/v1/discounts')) });
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  const [adding, setAdding] = useState<'opening_balance' | 'adhoc' | null>(null);
  const [discountId, setDiscountId] = useState('');
  const refreshDiscounts = () => void queryClient.invalidateQueries({ queryKey: ['student-discounts', studentId] });
  const give = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/students/{student_id}/discounts', { ...path, body: { discount_id: discountId } })),
    onSuccess: () => {
      setDiscountId('');
      refreshDiscounts();
    },
  });
  const take = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.DELETE('/api/v1/students/{student_id}/discounts/{discount_id}', { params: { path: { student_id: studentId, discount_id: id } } })),
    onSuccess: refreshDiscounts,
  });

  if (account.isPending) return <Loading />;
  if (account.isError) return <ErrorAlert error={account.error} />;
  const a = account.data;
  const available = (discounts.data ?? []).filter((d) => d.is_active && d.rule === 'manual' && !given.data?.some((g) => g.id === d.id));

  return (
    <div className="card section">
      <div className="row between">
        <h2>Fees</h2>
        {issue && (
          <div className="row">
            <button className="secondary small" onClick={() => setAdding('adhoc')}>
              One-off charge
            </button>
            {!a.invoices.some((i) => i.kind === 'opening_balance' && i.status !== 'void') && (
              <button className="secondary small" onClick={() => setAdding('opening_balance')}>
                Opening balance
              </button>
            )}
          </div>
        )}
      </div>
      <div className="stats compact">
        <div>
          <div className="muted small">Owed</div>
          <div className={`stat-value ${a.outstanding_kobo > 0 ? 'error-text' : ''}`}>{formatKobo(a.outstanding_kobo)}</div>
        </div>
        <div>
          <div className="muted small">Billed</div>
          <div className="stat-value">{formatKobo(a.billed_kobo)}</div>
        </div>
        <div>
          <div className="muted small">Paid</div>
          <div className="stat-value">{formatKobo(a.paid_kobo)}</div>
        </div>
      </div>
      {a.invoices.length === 0 ? (
        <p className="muted">No invoices yet.</p>
      ) : (
        <table className="compact">
          <tbody>
            {a.invoices.map((i) => (
              <tr key={i.id}>
                <td>
                  <Link to={`/console/invoices/${i.id}`}>#{i.number}</Link> <span className="muted small">{INVOICE_KIND_LABELS[i.kind]}</span>
                </td>
                <td className="num">{formatKobo(i.total_kobo)}</td>
                <td className="num">{formatKobo(i.balance_kobo)} left</td>
                <td>
                  <StatusBadge status={i.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Discounts</h3>
      <ErrorAlert error={give.error ?? take.error} />
      {(given.data ?? []).length === 0 && <p className="muted small">None. Sibling discounts are applied automatically when invoices are generated.</p>}
      <ul className="plain">
        {(given.data ?? []).map((d) => (
          <li key={d.id}>
            {d.name} <span className="muted small">({describeDiscount(d, items.data)})</span>{' '}
            {manageFees && (
              <button className="link small" onClick={() => take.mutate(d.id)}>
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {manageFees && available.length > 0 && (
        <div className="row">
          <select aria-label="Discount" value={discountId} onChange={(e) => setDiscountId(e.target.value)}>
            <option value="">Give a discount…</option>
            {available.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <button className="secondary small" disabled={!discountId || give.isPending} onClick={() => give.mutate()}>
            Give
          </button>
        </div>
      )}
      {adding && <NewInvoice studentId={studentId} kind={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}

function NewInvoice({ studentId, kind, onClose }: { studentId: string; kind: 'opening_balance' | 'adhoc'; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(kind === 'opening_balance' ? 'Balance brought forward' : '');
  const [amount, setAmount] = useState('');
  const kobo = parseNairaToKobo(amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/invoices', { body: { student_id: studentId, kind, lines: [{ description, amount_kobo: kobo ?? 0 }] } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account', studentId] });
      void queryClient.invalidateQueries({ queryKey: ['invoices'] });
      onClose();
    },
  });
  return (
    <Modal title={kind === 'opening_balance' ? 'Opening balance' : 'One-off charge'} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted">
          {kind === 'opening_balance'
            ? 'What the student already owed before the school started using SchoolPay. Payments clear the oldest debts first.'
            : 'A charge outside the term fees, e.g. an excursion or a replacement ID card.'}
        </p>
        <ErrorAlert error={save.error} />
        <Field label="Description">
          <input value={description} onChange={(e) => setDescription(e.target.value)} required minLength={2} />
        </Field>
        <Field label="Amount (₦)" error={amount && kobo === null ? 'Not a valid amount' : undefined}>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!kobo || save.isPending}>
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
