import { useMutation, useQuery } from '@tanstack/react-query';

import { api, unwrap, type Schemas } from '../api/client';
import { saveBlob } from '../lib/download';
import { formatDate, formatKobo } from '../lib/format';
import { Empty, ErrorAlert, Loading, PageHeader } from '../lib/ui';

type Invoice = Schemas['SubscriptionInvoiceOut'];

/** What the school pays for SchoolPay: its plan and its termly bills. */
export function Subscription() {
  const sub = useQuery({ queryKey: ['subscription'], queryFn: async () => unwrap(await api.GET('/api/v1/subscription')) });
  const pdf = useMutation({
    mutationFn: async (invoice: Invoice) => {
      const { data, response } = await api.GET('/api/v1/subscription/invoices/{invoice_id}/pdf', {
        params: { path: { invoice_id: invoice.id } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error('Could not download the invoice.');
      saveBlob(data as Blob, `schoolpay-invoice-${invoice.number}.pdf`);
    },
  });
  if (sub.isPending) return <Loading />;
  if (sub.isError) return <ErrorAlert error={sub.error} />;
  const { plan, pay_to: payTo } = sub.data;
  const owed = sub.data.invoices.reduce((sum, i) => sum + i.balance_kobo, 0);
  return (
    <>
      <PageHeader title="Subscription" subtitle="What your school pays for SchoolPay. Parents never pay anything extra." />
      <div className="grid-2">
        <section className="card" aria-label="Your plan">
          <h2>{plan.name} plan</h2>
          <dl className="details">
            <dt>Set-up fee</dt>
            <dd>{formatKobo(plan.setup_fee_kobo)}, once</dd>
            <dt>Licence</dt>
            <dd>{formatKobo(plan.annual_fee_kobo)} per school year</dd>
            <dt>Per pupil</dt>
            <dd>
              {formatKobo(plan.per_pupil_term_kobo)} each term ({formatKobo(plan.per_pupil_year_kobo)} a year)
            </dd>
          </dl>
          <p className="muted small">
            With {sub.data.pupils_this_term} pupils this term, the pupil charge for a term is {formatKobo(sub.data.next_term_estimate_kobo)}.
          </p>
        </section>
        <section className="card" aria-label="How to pay">
          <h2>How to pay</h2>
          {payTo ? (
            <p>
              Bank transfer to <strong>{payTo.account_name}</strong>, account <strong>{payTo.account_number}</strong> ({payTo.bank_name}).
              Please quote the invoice number as the reference.
            </p>
          ) : (
            <p>Reply to the invoice email and we'll send our bank details.</p>
          )}
          {owed > 0 && (
            <p>
              Still to pay: <strong>{formatKobo(owed)}</strong>
            </p>
          )}
        </section>
      </div>
      <h2 className="section-title">Bills</h2>
      <ErrorAlert error={pdf.error} />
      {sub.data.invoices.length === 0 ? (
        <Empty>No bills yet. You'll get one by email at the start of each term.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Term</th>
                <th className="num">Pupils</th>
                <th className="num">Total</th>
                <th className="num">Still owed</th>
                <th>Due</th>
              </tr>
            </thead>
            <tbody>
              {sub.data.invoices.map((i) => (
                <tr key={i.id} className={i.status === 'void' ? 'retired' : ''}>
                  <td>
                    <button className="link" onClick={() => pdf.mutate(i)}>
                      {i.number} (PDF)
                    </button>
                    {i.status !== 'unpaid' && (
                      <span className={`badge ${i.status === 'paid' ? 'ok' : i.status === 'overdue' ? 'danger' : ''}`}>
                        {i.status === 'paid' ? 'Paid' : i.status === 'overdue' ? 'Overdue' : 'Void'}
                      </span>
                    )}
                  </td>
                  <td className="small">{i.period}</td>
                  <td className="num">{i.pupils}</td>
                  <td className="num">{formatKobo(i.total_kobo)}</td>
                  <td className="num">{formatKobo(i.balance_kobo)}</td>
                  <td className="nowrap small">{formatDate(i.due_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
