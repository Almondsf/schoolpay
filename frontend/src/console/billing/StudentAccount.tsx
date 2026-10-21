import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { BRAND } from '../../lib/brand';
import { INVOICE_KIND_LABELS, formatKobo, koboToInput, parseNairaToKobo } from '../../lib/format';
import { ErrorAlert, Field, Loading, Modal, StatusBadge, fieldError } from '../../lib/ui';
import { describeDiscount } from '../fees/FeeSetup';
import { useCurrentTerm } from '../hooks';
import { FeeTable } from '../../lib/FeeBreakdown';

/** A student's fees: balance across all invoices (arrears included), each invoice, and their discounts. */
export function StudentAccount({ studentId }: { studentId: string }) {
  const { can } = useAuth();
  const issue = can('invoices.issue');
  const manageFees = can('fees.manage');
  const queryClient = useQueryClient();
  const path = { params: { path: { student_id: studentId } } };
  const account = useQuery({
    queryKey: ['account', studentId],
    queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}/account', path)),
  });
  const given = useQuery({
    queryKey: ['student-discounts', studentId],
    queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}/discounts', path)),
  });
  const discounts = useQuery({
    queryKey: ['discounts'],
    enabled: manageFees,
    queryFn: async () => unwrap(await api.GET('/api/v1/discounts')),
  });
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  // Under the 'account' key so everything that refreshes the account refreshes this too
  const breakdown = useQuery({
    queryKey: ['account', studentId, 'fee-breakdown'],
    queryFn: async () => unwrap(await api.GET('/api/v1/reports/students/{student_id}/fee-breakdown', path)),
  });
  const [adding, setAdding] = useState<'opening_balance' | 'charge' | 'exempt' | null>(null);
  const [discountId, setDiscountId] = useState('');
  const refreshDiscounts = () => void queryClient.invalidateQueries({ queryKey: ['student-discounts', studentId] });
  const give = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/students/{student_id}/discounts', { ...path, body: { discount_id: discountId } })),
    onSuccess: () => {
      setDiscountId('');
      refreshDiscounts();
    },
  });
  const take = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.DELETE('/api/v1/students/{student_id}/discounts/{discount_id}', {
          params: { path: { student_id: studentId, discount_id: id } },
        }),
      ),
    onSuccess: refreshDiscounts,
  });

  if (account.isPending) return <Loading />;
  if (account.isError) return <ErrorAlert error={account.error} />;
  const a = account.data;
  const available = (discounts.data ?? []).filter((d) => d.is_active && d.rule === 'manual' && !given.data?.some((g) => g.id === d.id));

  return (
    <>
      <div className="card section">
        <div className="row between">
          <h2>{breakdown.data?.term_label ? `${breakdown.data.term_label} fees` : 'Fees'}</h2>
          {issue && (
            <div className="row">
              <button className="secondary small" onClick={() => setAdding('charge')}>
                Add a charge
              </button>
              {!a.invoices.some((i) => i.kind === 'opening_balance' && i.status !== 'void') && (
                <button className="secondary small" onClick={() => setAdding('opening_balance')}>
                  Opening balance
                </button>
              )}
            </div>
          )}
        </div>
        {breakdown.data && <FeeTable breakdown={breakdown.data} />}
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Bills</h2>
          {a.invoices.length === 0 ? (
            <p className="muted">No invoices yet.</p>
          ) : (
            <table className="compact">
              <tbody>
                {a.invoices.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <Link to={`/console/invoices/${i.id}`}>#{i.number}</Link>{' '}
                      <span className="muted small">{INVOICE_KIND_LABELS[i.kind]}</span>
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
        </div>
        <div className="card">
          <h2>Discounts</h2>
          <ErrorAlert error={give.error ?? take.error} />
          {(given.data ?? []).length === 0 && (
            <p className="muted small">None. Sibling discounts are applied automatically when invoices are generated.</p>
          )}
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
          <Exemptions studentId={studentId} canChange={issue} onExempt={() => setAdding('exempt')} />
          {adding === 'opening_balance' && <NewInvoice studentId={studentId} kind={adding} onClose={() => setAdding(null)} />}
          {adding === 'charge' && <AddCharge studentId={studentId} onClose={() => setAdding(null)} />}
          {adding === 'exempt' && <Exempt studentId={studentId} onClose={() => setAdding(null)} />}
        </div>
      </div>
    </>
  );
}

function NewInvoice({ studentId, kind, onClose }: { studentId: string; kind: 'opening_balance'; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(kind === 'opening_balance' ? 'Balance brought forward' : '');
  const [amount, setAmount] = useState('');
  const kobo = parseNairaToKobo(amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/invoices', { body: { student_id: studentId, kind, lines: [{ description, amount_kobo: kobo ?? 0 }] } }),
      ),
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
            ? `What the student already owed before the school started using ${BRAND}. Payments clear the oldest debts first.`
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

function useRefreshAccount(studentId: string) {
  const queryClient = useQueryClient();
  return () => {
    for (const key of [['account', studentId], ['exemptions', studentId], ['invoices'], ['billing-summary']]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };
}

/** An item for this pupil: from the price list, or typed in; it goes on this term's bill (or a one-off bill). */
function AddCharge({ studentId, onClose }: { studentId: string; onClose: () => void }) {
  const refresh = useRefreshAccount(studentId);
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  const priced = (items.data ?? []).filter((i) => i.is_active && i.default_amount_kobo);
  const [itemId, setItemId] = useState('');
  const [description, setDescription] = useState('');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState(1);
  const [reason, setReason] = useState('');
  const unit = parseNairaToKobo(price);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/students/{student_id}/charges', {
          params: { path: { student_id: studentId } },
          body: { fee_item_id: itemId || null, description, unit_kobo: unit, quantity, reason },
        }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  const choose = (id: string) => {
    setItemId(id);
    const item = priced.find((i) => i.id === id);
    setDescription(item ? '' : description);
    setPrice(item?.default_amount_kobo ? koboToInput(item.default_amount_kobo) : '');
  };
  return (
    <Modal title="Add a charge" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted small">It goes on this term's bill, or on a bill of its own if the pupil hasn't been billed this term.</p>
        <Field label="Item">
          <select value={itemId} onChange={(e) => choose(e.target.value)}>
            <option value="">Something else (type it below)</option>
            {priced.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name} ({formatKobo(i.default_amount_kobo ?? 0)})
              </option>
            ))}
          </select>
        </Field>
        {!itemId && (
          <Field label="What it's for" error={fieldError(save.error, 'description')}>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
              maxLength={200}
              placeholder="e.g. Textbooks"
            />
          </Field>
        )}
        <div className="grid-2 tight">
          <Field
            label="Price (₦)"
            error={fieldError(save.error, 'unit_kobo') ?? (price && unit === null ? 'Not a valid amount' : undefined)}
          >
            <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" required />
          </Field>
          <Field label="Quantity">
            <input type="number" min={1} max={100} value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} />
          </Field>
        </div>
        {unit !== null && unit > 0 && quantity > 1 && <p className="small">Total: {formatKobo(unit * quantity)}</p>}
        <Field label="Note (optional)">
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
        </Field>
        <ErrorAlert
          error={save.error && !fieldError(save.error, 'description') && !fieldError(save.error, 'unit_kobo') ? save.error : null}
        />
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!unit || save.isPending}>
            Add charge
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Class fees this pupil doesn't pay (e.g. not sitting WAEC). */
function Exemptions({ studentId, canChange, onExempt }: { studentId: string; canChange: boolean; onExempt: () => void }) {
  const refresh = useRefreshAccount(studentId);
  const list = useQuery({
    queryKey: ['exemptions', studentId],
    queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}/exemptions', { params: { path: { student_id: studentId } } })),
  });
  const undo = useMutation({
    mutationFn: async (e: { term_id: string; fee_item_id: string }) =>
      unwrap(
        await api.DELETE('/api/v1/students/{student_id}/exemptions/{fee_item_id}', {
          params: { path: { student_id: studentId, fee_item_id: e.fee_item_id }, query: { term_id: e.term_id } },
        }),
      ),
    onSuccess: refresh,
  });
  return (
    <>
      <div className="row between">
        <h3>Not paying this term</h3>
        {canChange && (
          <button className="link small" onClick={onExempt}>
            Exempt from a fee
          </button>
        )}
      </div>
      <ErrorAlert error={undo.error} />
      {(list.data ?? []).length === 0 ? (
        <p className="muted small">None. Pupils pay every fee for their class unless exempted here.</p>
      ) : (
        <ul className="plain">
          {(list.data ?? []).map((e) => (
            <li key={`${e.term_id}-${e.fee_item_id}`}>
              {e.item_name} {e.reason && <span className="muted small">({e.reason})</span>}{' '}
              {canChange && (
                <button className="link small" onClick={() => undo.mutate(e)} disabled={undo.isPending}>
                  Charge again
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Exempt({ studentId, onClose }: { studentId: string; onClose: () => void }) {
  const refresh = useRefreshAccount(studentId);
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  const term = useCurrentTerm();
  const [itemId, setItemId] = useState('');
  const [reason, setReason] = useState('');
  const [done, setDone] = useState<number | null>(null);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/students/{student_id}/exemptions', {
          params: { path: { student_id: studentId } },
          body: { term_id: term.data?.id ?? '', fee_item_id: itemId, reason },
        }),
      ),
    onSuccess: (data) => {
      refresh();
      setDone(data.changed_kobo);
    },
  });
  return (
    <Modal title="Exempt from a fee this term" onClose={onClose}>
      {done !== null ? (
        <>
          <p>{done > 0 ? `${formatKobo(done)} was taken off this term's bill.` : 'Done. It will be left off when the term is billed.'}</p>
          <div className="row end">
            <button onClick={onClose}>Close</button>
          </div>
        </>
      ) : (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Fee">
            <select value={itemId} onChange={(e) => setItemId(e.target.value)} required>
              <option value="">Choose…</option>
              {(items.data ?? [])
                .filter((i) => i.is_active)
                .map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Reason (kept with the bill)">
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={300}
              placeholder="e.g. Not going on the excursion"
            />
          </Field>
          <ErrorAlert error={save.error} />
          <div className="row end">
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={!itemId || !term.data || save.isPending}>
              Exempt
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
