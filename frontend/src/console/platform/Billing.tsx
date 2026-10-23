import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { saveBlob } from '../../lib/download';
import { formatDate, formatKobo, lagosToday, parseNairaToKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, fieldError } from '../../lib/ui';

type Invoice = Schemas['SubscriptionInvoiceOut'];
type Status = Invoice['status'];
const PAGE = 50;
const STATUS_TONE: Record<Status, string> = { paid: 'ok', unpaid: 'warn', overdue: 'danger', void: '' };

/** Subscription bills: what schools owe WardPay each term. */
export function PlatformBilling() {
  const [params, setParams] = useSearchParams();
  const status = (params.get('status') as Status | null) ?? '';
  const [offset, setOffset] = useState(0);
  const [generating, setGenerating] = useState(false);
  const invoices = useQuery({
    queryKey: ['platform-invoices', status, offset],
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/platform/billing/invoices', { params: { query: { status: status || undefined, limit: PAGE, offset } } })),
    staleTime: 0,
  });
  return (
    <>
      <PageHeader
        title="Subscription billing"
        subtitle="Once a term, bill every active school: the set-up fee on its first bill, and the pupils enrolled this term."
        actions={<button onClick={() => setGenerating(true)}>Bill this term</button>}
      />
      <div className="filters">
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => {
            setParams(e.target.value ? { status: e.target.value } : {});
            setOffset(0);
          }}
        >
          <option value="">All bills</option>
          <option value="unpaid">Unpaid</option>
          <option value="overdue">Overdue</option>
          <option value="paid">Paid</option>
          <option value="void">Void</option>
        </select>
      </div>
      {invoices.isPending ? (
        <Loading />
      ) : invoices.isError ? (
        <ErrorAlert error={invoices.error} />
      ) : (
        <>
          <InvoiceTable invoices={invoices.data.items} showSchool />
          <Pager total={invoices.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </>
      )}
      {generating && <Generate onClose={() => setGenerating(false)} />}
    </>
  );
}

export function InvoiceTable({ invoices, showSchool = false }: { invoices: Invoice[]; showSchool?: boolean }) {
  const [paying, setPaying] = useState<Invoice | null>(null);
  const [voiding, setVoiding] = useState<Invoice | null>(null);
  const [adjusting, setAdjusting] = useState<Invoice | null>(null);
  const pdf = useMutation({
    mutationFn: async (invoice: Invoice) => {
      const { data, response } = await api.GET('/api/v1/platform/billing/invoices/{invoice_id}/pdf', {
        params: { path: { invoice_id: invoice.id } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error('Could not download the invoice.');
      saveBlob(data as Blob, `wardpay-invoice-${invoice.number}.pdf`);
    },
  });
  if (invoices.length === 0) return <Empty>No bills yet.</Empty>;
  return (
    <>
      <ErrorAlert error={pdf.error} />
      <div className="card table-card">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>No.</th>
                {showSchool && <th>School</th>}
                <th>Term</th>
                <th className="num">Pupils</th>
                <th className="num">Total</th>
                <th className="num">Still owed</th>
                <th>Due</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {invoices.map((i) => (
                <tr key={i.id} className={i.status === 'void' ? 'retired' : ''}>
                  <td>
                    <button className="link" onClick={() => pdf.mutate(i)} title="Download PDF">
                      {i.number}
                    </button>
                  </td>
                  {showSchool && (
                    <td>
                      <Link to={`/console/platform/schools/${i.school_id}`}>{i.school_name}</Link>
                      <div className="muted small">{i.plan_name}</div>
                    </td>
                  )}
                  <td className="small">{i.period}</td>
                  <td className="num">{i.pupils}</td>
                  <td className="num">{formatKobo(i.total_kobo)}</td>
                  <td className="num">{formatKobo(i.balance_kobo)}</td>
                  <td className="nowrap small">{formatDate(i.due_date)}</td>
                  <td>
                    <span className={`badge ${STATUS_TONE[i.status]}`}>{i.status === 'void' ? 'Void' : i.status[0]!.toUpperCase() + i.status.slice(1)}</span>
                  </td>
                  <td className="actions">
                    {(i.status === 'unpaid' || i.status === 'overdue') && (
                      <>
                        <button className="secondary small" onClick={() => setPaying(i)}>
                          Record payment
                        </button>
                        <button className="link small" onClick={() => setAdjusting(i)}>
                          Adjust
                        </button>
                        {i.paid_kobo === 0 && (
                          <button className="link small" onClick={() => setVoiding(i)}>
                            Void
                          </button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {paying && <RecordPayment invoice={paying} onClose={() => setPaying(null)} />}
      {voiding && <VoidInvoice invoice={voiding} onClose={() => setVoiding(null)} />}
      {adjusting && <AdjustInvoice invoice={adjusting} onClose={() => setAdjusting(null)} />}
    </>
  );
}

function useRefreshBilling() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ['platform-invoices', 'platform-overview', 'platform-schools', 'platform-school']) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

function Generate({ onClose }: { onClose: () => void }) {
  const refresh = useRefreshBilling();
  const run = useMutation({
    mutationFn: async (dryRun: boolean) => unwrap(await api.POST('/api/v1/platform/billing/generate', { body: { dry_run: dryRun } })),
    onSuccess: (data) => {
      if (!data.dry_run) refresh();
    },
  });
  const report = run.data;
  const finished = report && !report.dry_run;
  return (
    <Modal title="Bill this term" onClose={onClose}>
      <p>
        Every active school is billed for its current term. Schools already billed for their term are skipped, so this is safe to run again
        (for example after a school sets its calendar). Each school's proprietor is emailed their bill.
      </p>
      <ErrorAlert error={run.error} />
      {report && (
        <div className="card inset">
          <strong>
            {finished ? 'Done: ' : 'Preview: '}
            {report.bills.length} bills totalling {formatKobo(report.total_kobo)}
          </strong>
          {report.already_billed > 0 && <div className="muted">{report.already_billed} schools were already billed for this term.</div>}
          {report.no_current_term.length > 0 && (
            <div className="muted small">No current term set, so not billed: {report.no_current_term.join(', ')}.</div>
          )}
          {report.bills.length > 0 && (
            <table className="compact">
              <tbody>
                {report.bills.map((b) => (
                  <tr key={b.school_id}>
                    <td>
                      {b.school_name}
                      <div className="muted small">{b.lines.map((ln) => ln.description).join(' · ')}</div>
                    </td>
                    <td className="num">{b.pupils} pupils</td>
                    <td className="num">{formatKobo(b.total_kobo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          {finished ? 'Close' : 'Cancel'}
        </button>
        {!finished && (
          <>
            <button className="secondary" onClick={() => run.mutate(true)} disabled={run.isPending}>
              Preview
            </button>
            <button onClick={() => run.mutate(false)} disabled={run.isPending || !report || report.bills.length === 0}>
              Send {report ? report.bills.length : ''} bills
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}

function RecordPayment({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const refresh = useRefreshBilling();
  const [amount, setAmount] = useState(String(invoice.balance_kobo / 100));
  const [receivedOn, setReceivedOn] = useState(lagosToday());
  const [reference, setReference] = useState('');
  const kobo = parseNairaToKobo(amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/platform/billing/invoices/{invoice_id}/payments', {
          params: { path: { invoice_id: invoice.id } },
          body: { amount_kobo: kobo ?? 0, received_on: receivedOn, reference },
        }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title={`Payment for invoice ${invoice.number}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>
          {invoice.school_name} still owes <strong>{formatKobo(invoice.balance_kobo)}</strong>. Record a transfer once it's in the
          platform's account.
        </p>
        <ErrorAlert error={fieldError(save.error, 'amount_kobo') ? null : save.error} />
        <Field label="Amount (₦)" error={fieldError(save.error, 'amount_kobo') ?? (amount && kobo === null ? 'Not a valid amount' : undefined)}>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required />
        </Field>
        <Field label="Received on">
          <input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} required />
        </Field>
        <Field label="Bank reference (optional)">
          <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={100} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!kobo || save.isPending}>
            Record payment
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Take money off a bill (a discount line; waiving the set-up fee is one click) or move its due date. */
function AdjustInvoice({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const refresh = useRefreshBilling();
  const path = { params: { path: { invoice_id: invoice.id } } };
  const detail = useQuery({
    queryKey: ['platform-invoice', invoice.id],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/billing/invoices/{invoice_id}', path)),
  });
  const setupLine = detail.data?.lines.find((ln) => ln.kind === 'setup');
  const alreadyDiscounted = detail.data?.lines.some((ln) => ln.kind === 'discount');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [dueDate, setDueDate] = useState(invoice.due_date);
  const [dueReason, setDueReason] = useState('');
  const kobo = parseNairaToKobo(amount);
  const done = () => {
    refresh();
    onClose();
  };
  const discount = useMutation({
    mutationFn: async (body: { amount_kobo: number; reason: string }) =>
      unwrap(await api.POST('/api/v1/platform/billing/invoices/{invoice_id}/discount', { ...path, body })),
    onSuccess: done,
  });
  const due = useMutation({
    mutationFn: async () =>
      unwrap(await api.PUT('/api/v1/platform/billing/invoices/{invoice_id}/due-date', { ...path, body: { due_date: dueDate, reason: dueReason } })),
    onSuccess: done,
  });
  return (
    <Modal title={`Adjust invoice ${invoice.number}`} onClose={onClose}>
      <p>
        {invoice.school_name} still owes <strong>{formatKobo(invoice.balance_kobo)}</strong>. Changes are kept in the activity log and show
        on the school's bill.
      </p>
      <section aria-label="Discount">
        <h3>Take money off</h3>
        {setupLine && !alreadyDiscounted && setupLine.amount_kobo <= invoice.balance_kobo && (
          <p>
            <button
              type="button"
              className="secondary small"
              onClick={() => discount.mutate({ amount_kobo: setupLine.amount_kobo, reason: 'Set-up fee waived' })}
              disabled={discount.isPending}
            >
              Waive the set-up fee ({formatKobo(setupLine.amount_kobo)})
            </button>
          </p>
        )}
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (kobo) discount.mutate({ amount_kobo: kobo, reason });
          }}
        >
          <ErrorAlert error={fieldError(discount.error, 'amount_kobo') ? null : discount.error} />
          <Field label="Discount (₦)" error={fieldError(discount.error, 'amount_kobo') ?? (amount && kobo === null ? 'Not a valid amount' : undefined)}>
            <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required />
          </Field>
          <Field label="Reason (shown on the bill)">
            <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} maxLength={150} placeholder="e.g. First-year discount" />
          </Field>
          <div className="row end">
            <button type="submit" disabled={!kobo || discount.isPending}>
              Take it off
            </button>
          </div>
        </form>
      </section>
      <section aria-label="Due date">
        <h3>Due date</h3>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            due.mutate();
          }}
        >
          <ErrorAlert error={fieldError(due.error, 'due_date') ? null : due.error} />
          <Field label="Due on" error={fieldError(due.error, 'due_date')}>
            <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} required />
          </Field>
          <Field label="Reason (kept in the activity log)">
            <input value={dueReason} onChange={(e) => setDueReason(e.target.value)} required minLength={3} maxLength={300} />
          </Field>
          <div className="row end">
            <button type="button" className="secondary" onClick={onClose}>
              Close
            </button>
            <button type="submit" disabled={dueDate === invoice.due_date || due.isPending}>
              Change due date
            </button>
          </div>
        </form>
      </section>
    </Modal>
  );
}

function VoidInvoice({ invoice, onClose }: { invoice: Invoice; onClose: () => void }) {
  const refresh = useRefreshBilling();
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/platform/billing/invoices/{invoice_id}/void', { params: { path: { invoice_id: invoice.id } }, body: { reason } })),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title={`Void invoice ${invoice.number}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>The bill is cancelled (it stays on record as void). Billing the term again then makes a new one.</p>
        <ErrorAlert error={save.error} />
        <Field label="Reason (kept in the activity log)">
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
