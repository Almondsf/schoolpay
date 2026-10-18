import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatDateTime, formatKobo, humanize, parseNairaToKobo } from '../../lib/format';
import { ErrorAlert, Field, Loading, Modal, PageHeader, StatusBadge } from '../../lib/ui';

type Invoice = Schemas['InvoiceOut'];

export function InvoiceDetail() {
  const { invoiceId = '' } = useParams();
  const { can } = useAuth();
  const issue = can('invoices.issue');
  const invoice = useQuery({
    queryKey: ['invoice', invoiceId],
    queryFn: async () => unwrap(await api.GET('/api/v1/invoices/{invoice_id}', { params: { path: { invoice_id: invoiceId } } })),
  });
  const [dialog, setDialog] = useState<'charge' | 'adjust' | 'void' | null>(null);

  if (invoice.isPending) return <Loading />;
  if (invoice.isError) return <ErrorAlert error={invoice.error} />;
  const i = invoice.data;
  const live = i.status !== 'void';

  return (
    <>
      <p>
        <Link to="/console/billing">← Billing</Link>
      </p>
      <PageHeader
        title={`Invoice #${i.number}`}
        subtitle={
          <>
            <Link to={`/console/students/${i.student.id}`}>{i.student.full_name}</Link> ({i.student.admission_number}) · {humanize(i.kind)} · issued{' '}
            {formatDate(i.issued_at)}
            {i.due_date && ` · due ${formatDate(i.due_date)}`} · <StatusBadge status={i.status} />
          </>
        }
        actions={
          issue &&
          live && (
            <>
              <button className="secondary" onClick={() => setDialog('charge')}>
                Add charge
              </button>
              <button className="secondary" onClick={() => setDialog('adjust')}>
                Correct amount
              </button>
              {i.paid_kobo === 0 && (
                <button className="secondary" onClick={() => setDialog('void')}>
                  Void
                </button>
              )}
            </>
          )
        }
      />
      {i.void_reason && <div className="alert error">Voided: {i.void_reason}</div>}
      <div className="card table-card">
        <table>
          <thead>
            <tr>
              <th>Item</th>
              <th>Added</th>
              <th className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {i.lines.map((line) => (
              <tr key={line.id}>
                <td>
                  {line.description}
                  {line.kind !== 'fee' && <span className="muted small"> ({line.kind})</span>}
                  {line.reason && <div className="muted small">{line.reason}</div>}
                </td>
                <td className="muted small">{formatDateTime(line.created_at)}</td>
                <td className="num">{formatKobo(line.amount_kobo)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th colSpan={2}>Total</th>
              <th className="num">{formatKobo(i.total_kobo)}</th>
            </tr>
            <tr>
              <td colSpan={2}>Paid</td>
              <td className="num">{formatKobo(i.paid_kobo)}</td>
            </tr>
            <tr>
              <th colSpan={2}>Balance</th>
              <th className="num">{formatKobo(i.balance_kobo)}</th>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="hint">Invoice lines are never edited or deleted. Corrections are added as new lines, so the history is always visible.</p>
      {dialog === 'void' && <VoidInvoice invoice={i} onClose={() => setDialog(null)} />}
      {(dialog === 'charge' || dialog === 'adjust') && <AddLine invoice={i} kind={dialog === 'charge' ? 'fee' : 'adjustment'} onClose={() => setDialog(null)} />}
    </>
  );
}

function useRefresh(invoice: Invoice) {
  const queryClient = useQueryClient();
  return (data: Invoice) => {
    queryClient.setQueryData(['invoice', invoice.id], data);
    void queryClient.invalidateQueries({ queryKey: ['invoices'] });
    void queryClient.invalidateQueries({ queryKey: ['billing-summary'] });
    void queryClient.invalidateQueries({ queryKey: ['account', invoice.student.id] });
  };
}

function AddLine({ invoice, kind, onClose }: { invoice: Invoice; kind: 'fee' | 'adjustment'; onClose: () => void }) {
  const refresh = useRefresh(invoice);
  const [description, setDescription] = useState('');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const kobo = parseNairaToKobo(amount, { allowNegative: kind === 'adjustment' });
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/invoices/{invoice_id}/lines', {
          params: { path: { invoice_id: invoice.id } },
          body: { kind, description, amount_kobo: kobo ?? 0, reason },
        }),
      ),
    onSuccess: (data) => {
      refresh(data);
      onClose();
    },
  });
  return (
    <Modal title={kind === 'fee' ? 'Add a charge' : 'Correct the amount'} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <ErrorAlert error={save.error} />
        <Field label="Description" hint={kind === 'fee' ? 'e.g. Replacement ID card' : 'e.g. PTA levy waived'}>
          <input value={description} onChange={(e) => setDescription(e.target.value)} required minLength={2} />
        </Field>
        <Field
          label="Amount (₦)"
          hint={kind === 'adjustment' ? 'Negative to reduce the invoice (e.g. -5000), positive to increase it.' : undefined}
          error={amount && kobo === null ? 'Not a valid amount' : undefined}
        >
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required />
        </Field>
        <Field label="Reason (kept with the invoice)">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
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

function VoidInvoice({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const refresh = useRefresh(invoice);
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/invoices/{invoice_id}/void', { params: { path: { invoice_id: invoice.id } }, body: { reason } })),
    onSuccess: (data) => {
      refresh(data);
      onClose();
    },
  });
  return (
    <Modal title={`Void invoice #${invoice.number}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>Use this for an invoice issued by mistake. It stays on record, marked void, and no longer counts as owed.</p>
        <ErrorAlert error={save.error} />
        <Field label="Reason">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="danger" disabled={save.isPending}>
            Void invoice
          </button>
        </div>
      </form>
    </Modal>
  );
}
