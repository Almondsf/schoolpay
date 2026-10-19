import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, Navigate, Outlet, Route, Routes, useParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { saveBlob } from '../lib/download';
import { PlanSchedule } from '../lib/PlanSchedule';
import { INVOICE_KIND_LABELS, formatDate, formatKobo, humanize, parseNairaToKobo } from '../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, StatusBadge } from '../lib/ui';
import { ParentAuthProvider, useParentAuth } from './ParentAuth';
import { FamilyPayment } from '../public/FamilyPay';
import { ParentLogin } from './ParentLogin';

type Child = Schemas['ChildOut'];

/** The parent portal: mobile-first, phone sign-in, all of a parent's children in one place. */
export function ParentApp() {
  return (
    <ParentAuthProvider>
      <Routes>
        <Route path="login" element={<ParentLogin />} />
        <Route element={<RequireParent />}>
          <Route index element={<Home />} />
          <Route path="children/:studentId" element={<ChildPage />} />
          <Route path="pay" element={<PayAll />} />
          <Route path="*" element={<Navigate to="/parent" replace />} />
        </Route>
      </Routes>
    </ParentAuthProvider>
  );
}

function RequireParent() {
  const { signedIn, loading, signOut } = useParentAuth();
  if (loading) return <Loading />;
  if (!signedIn) return <Navigate to="/parent/login" replace />;
  return (
    <div className="parent-shell">
      <header className="parent-bar">
        <Link to="/parent" className="brand-link">
          School fees
        </Link>
        <button className="link small light" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <main className="parent-page">
        <Outlet />
      </main>
    </div>
  );
}

const useHome = () => useQuery({ queryKey: ['parent-home'], queryFn: async () => unwrap(await api.GET('/api/v1/parent/home')) });

function Home() {
  const home = useHome();
  if (home.isPending) return <Loading />;
  if (home.isError) return <ErrorAlert error={home.error} />;
  const totalOwed = home.data.children.reduce((sum, c) => sum + c.owed_kobo, 0);
  const owing = home.data.children.filter((c) => c.owed_kobo > 0).length;
  return (
    <>
      <p className="muted">{home.data.school_name}</p>
      <h1>Hello, {home.data.guardian_name}</h1>
      {totalOwed > 0 && (
        <section className="card total-owed" aria-label="Pay for all your children">
          <div>
            {owing > 1 ? `Total owed for ${owing} children` : 'Total owed'}:{' '}
            <strong>{formatKobo(totalOwed)}</strong>
          </div>
          <Link to="/parent/pay" className="button block">
            {owing > 1 ? 'Pay for all your children' : 'Pay now'}
          </Link>
          <p className="hint">One payment: bank transfer, card or USSD. You choose how much goes to each child.</p>
        </section>
      )}
      {home.data.children.length === 0 && <Empty>No children are linked to your number yet. Please contact the school.</Empty>}
      {home.data.children.map((child) => (
        <ChildCard key={child.id} child={child} />
      ))}
      <MessagePreferences />
    </>
  );
}

function MessagePreferences() {
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: ['parent-preferences'], queryFn: async () => unwrap(await api.GET('/api/v1/parent/preferences')) });
  const change = useMutation({
    mutationFn: async (body: Schemas['PreferenceIn']) => unwrap(await api.PUT('/api/v1/parent/preferences', { body })),
    onSuccess: (data) => queryClient.setQueryData(['parent-preferences'], data),
  });
  const download = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.GET('/api/v1/parent/my-data', { parseAs: 'blob' });
      if (!response.ok || !data) throw new Error('Could not download your data. Please try again later.');
      saveBlob(data as Blob, 'my-data.json');
    },
  });
  if (!prefs.data) return null;
  return (
    <section className="card section" aria-label="Messages and your data">
      <h2>Messages from the school</h2>
      <label className="checkbox">
        <input type="checkbox" checked={prefs.data.reminders} disabled={change.isPending} onChange={(e) => change.mutate({ reminders: e.target.checked })} />
        Send me new bills and payment reminders
      </label>
      <p className="muted small">You'll always get a receipt when a payment is received.</p>
      {prefs.data.calls_available && (
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={prefs.data.prefers_calls}
              disabled={change.isPending}
              onChange={(e) => change.mutate({ reminders: prefs.data.reminders, prefers_calls: e.target.checked })}
            />
            Also phone me: the school reads out what's owed and the account number, and confirms payments
          </label>
          {prefs.data.prefers_calls && (
            <Field label="Language on the call">
              <select
                value={prefs.data.language}
                disabled={change.isPending}
                onChange={(e) => change.mutate({ reminders: prefs.data.reminders, language: e.target.value as Schemas['Language'] })}
              >
                <option value="en">English</option>
                <option value="pcm">Pidgin</option>
              </select>
            </Field>
          )}
        </>
      )}
      <ErrorAlert error={change.error} />
      <h2>Your data</h2>
      <p className="small">
        Download everything the school holds about you and your children. <Link to="/privacy">How your data is used</Link>.
      </p>
      <button className="secondary small" onClick={() => download.mutate()} disabled={download.isPending}>
        Download my data
      </button>
      <ErrorAlert error={download.error} />
    </section>
  );
}

function ChildCard({ child }: { child: Child }) {
  const [paying, setPaying] = useState(false);
  return (
    <section className="card child-card">
      <div className="row between">
        <div>
          <h2>{child.full_name}</h2>
          <div className="muted small">
            {child.class_name ?? 'No class this term'} · {child.admission_number}
          </div>
        </div>
        <Link to={`/parent/children/${child.id}`} className="small">
          Details
        </Link>
      </div>
      <div className={`owed ${child.owed_kobo > 0 ? 'due' : 'clear'}`}>
        {child.owed_kobo > 0 ? (
          <>
            <span className="muted small">To pay</span>
            <strong>{formatKobo(child.owed_kobo)}</strong>
          </>
        ) : (
          <strong>All paid. Thank you!</strong>
        )}
        {child.credit_kobo > 0 && <span className="small">{formatKobo(child.credit_kobo)} credit towards next term</span>}
      </div>
      {child.pay_in && <TransferDetails account={child.pay_in} />}
      {child.owed_kobo > 0 && (
        <button className="block secondary" onClick={() => setPaying(true)}>
          Pay online for {child.first_name} only
        </button>
      )}
      {paying && <PayByCard child={child} onClose={() => setPaying(false)} />}
    </section>
  );
}

function TransferDetails({ account }: { account: Schemas['PayInAccount'] }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="transfer">
      <div className="muted small">Transfer to {account.bank_name}</div>
      <div className="row between">
        <span className="account-number">{account.account_number}</span>
        <button
          className="secondary small"
          onClick={() =>
            void navigator.clipboard?.writeText(account.account_number).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            })
          }
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <div className="small">{account.account_name}</div>
      <div className="hint">This number is for this child only. Payments show up within minutes and you'll get a receipt.</div>
    </div>
  );
}

function PayByCard({ child, onClose }: { child: Child; onClose: () => void }) {
  const [amount, setAmount] = useState('');
  const kobo = amount ? parseNairaToKobo(amount) : null;
  const pay = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/parent/children/{student_id}/pay', { params: { path: { student_id: child.id } }, body: { amount_kobo: kobo } })),
    onSuccess: (checkout) => window.location.assign(checkout.authorization_url),
  });
  return (
    <Modal title={`Pay for ${child.first_name}`} onClose={onClose}>
      <ErrorAlert error={pay.error} />
      <Field label="Amount (₦)" hint={`Leave empty to pay everything owed (${formatKobo(child.owed_kobo)}).`} error={amount && kobo === null ? 'Not a valid amount' : undefined}>
        <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>
      <p className="hint">On the payment provider's secure page, pay by bank transfer, card or USSD. We never see your card details.</p>
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          Cancel
        </button>
        <button onClick={() => pay.mutate()} disabled={pay.isPending || (!!amount && kobo === null)}>
          {pay.isPending ? 'Opening…' : 'Continue to pay'}
        </button>
      </div>
    </Modal>
  );
}

/** Pay for all the parent's children at once: the same page as their family payment link. */
function PayAll() {
  const family = useQuery({
    queryKey: ['parent-family'],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/parent/family')),
  });
  if (family.isPending) return <Loading />;
  if (family.isError) return <ErrorAlert error={family.error} />;
  return (
    <>
      <p>
        <Link to="/parent">← All children</Link>
      </p>
      <FamilyPayment
        data={family.data}
        checkout={async (items) => unwrap(await api.POST('/api/v1/parent/family/checkout', { body: { items } }))}
      />
    </>
  );
}

function ChildPage() {
  const { studentId = '' } = useParams();
  const account = useQuery({
    queryKey: ['parent-child', studentId],
    queryFn: async () => unwrap(await api.GET('/api/v1/parent/children/{student_id}', { params: { path: { student_id: studentId } } })),
  });
  const [openInvoice, setOpenInvoice] = useState<string | null>(null);
  if (account.isPending) return <Loading />;
  if (account.isError) return <ErrorAlert error={account.error} />;
  const { child, invoices, payments } = account.data;
  return (
    <>
      <p>
        <Link to="/parent">← All children</Link>
      </p>
      <ChildCard child={child} />
      <section className="card">
        <h2>Bills</h2>
        {invoices.length === 0 && <p className="muted">No bills yet.</p>}
        {invoices.map((inv) => (
          <div key={inv.id} className="list-item">
            <button className="link row between full" onClick={() => setOpenInvoice(openInvoice === inv.id ? null : inv.id)}>
              <span>
                {INVOICE_KIND_LABELS[inv.kind]} · #{inv.number}
                <span className="muted small"> {formatDate(inv.issued_at)}</span>
              </span>
              <StatusBadge status={inv.status} />
            </button>
            <div className="small">
              {formatKobo(inv.total_kobo)} · {inv.balance_kobo > 0 ? `${formatKobo(inv.balance_kobo)} left to pay` : 'paid'}
            </div>
            {inv.plan && (
              <div className="plan-box">
                <div className="small">
                  <strong>Paying in instalments</strong>
                </div>
                <PlanSchedule plan={inv.plan} />
              </div>
            )}
            {openInvoice === inv.id && <InvoiceLines invoiceId={inv.id} />}
          </div>
        ))}
      </section>
      <section className="card">
        <h2>Payments and receipts</h2>
        {payments.length === 0 && <p className="muted">No payments yet.</p>}
        {payments.map((p) => (
          <div key={p.id} className="list-item row between">
            <span>
              {formatKobo(p.amount_kobo)} <span className="muted small">{formatDate(p.received_at)} · {humanize(p.method)}</span>
              {p.reversed && <span className="badge danger">Reversed</span>}
            </span>
            {p.receipt_id && p.receipt_number && <ParentReceipt receiptId={p.receipt_id} number={p.receipt_number} />}
          </div>
        ))}
      </section>
    </>
  );
}

function InvoiceLines({ invoiceId }: { invoiceId: string }) {
  const invoice = useQuery({
    queryKey: ['parent-invoice', invoiceId],
    queryFn: async () => unwrap(await api.GET('/api/v1/parent/invoices/{invoice_id}', { params: { path: { invoice_id: invoiceId } } })),
  });
  if (!invoice.data) return <Loading />;
  return (
    <table className="compact lines">
      <tbody>
        {invoice.data.lines.map((line, i) => (
          <tr key={i}>
            <td>{line.description}</td>
            <td className="num">{formatKobo(line.amount_kobo)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ParentReceipt({ receiptId, number }: { receiptId: string; number: number }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.GET('/api/v1/parent/receipts/{receipt_id}/pdf', { params: { path: { receipt_id: receiptId } }, parseAs: 'blob' });
      if (!response.ok || !data) throw new Error('Could not download the receipt.');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(data as Blob);
      link.download = `receipt-${number}.pdf`;
      link.click();
      URL.revokeObjectURL(link.href);
    },
  });
  return (
    <button className="secondary small" onClick={() => download.mutate()} disabled={download.isPending}>
      Receipt #{number}
    </button>
  );
}
