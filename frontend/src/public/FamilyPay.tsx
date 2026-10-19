import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { BRAND } from '../lib/brand';
import { formatKobo, koboToInput, parseNairaToKobo } from '../lib/format';
import { ErrorAlert, Field, Loading } from '../lib/ui';

type Child = Schemas['FamilyChildOut'];

/**
 * A parent's family payment page, opened from the link in their bills and reminders
 * (no sign-in). Each child's account number for transfers, and one card payment for
 * any of them with the amount chosen per child.
 */
export function FamilyPay() {
  const { token = '' } = useParams();
  const page = useQuery({
    queryKey: ['family-pay', token],
    staleTime: 0,
    retry: false,
    queryFn: async () => unwrap(await api.GET('/api/v1/public/pay/{token}', { params: { path: { token } } })),
  });

  return (
    <div className="parent-shell">
      <header className="parent-bar">
        <strong>{page.data?.school_name ?? BRAND}</strong>
      </header>
      <main className="parent-page">
        {page.isPending ? <Loading /> : page.isError ? <ErrorAlert error={page.error} /> : <Family token={token} data={page.data} />}
      </main>
    </div>
  );
}

function Family({ token, data }: { token: string; data: Schemas['FamilyPageOut'] }) {
  const owing = data.children.filter((c) => c.owed_kobo > 0);
  // What the parent will pay per child, as typed (prefilled with what's due now)
  const [amounts, setAmounts] = useState<Record<string, string>>(() =>
    Object.fromEntries(owing.map((c) => [c.student_id, koboToInput(c.suggested_kobo)])),
  );
  const parsed = owing.map((c) => {
    const text = amounts[c.student_id] ?? '';
    const kobo = text.trim() === '' ? 0 : parseNairaToKobo(text);
    const problem =
      kobo === null
        ? 'Not a valid amount'
        : kobo > c.owed_kobo
          ? `Up to ${formatKobo(c.owed_kobo)}`
          : kobo > 0 && kobo < Math.min(data.min_part_kobo, c.owed_kobo)
            ? `At least ${formatKobo(data.min_part_kobo)}`
            : undefined;
    return { child: c, kobo: kobo ?? 0, problem };
  });
  const total = parsed.reduce((sum, p) => sum + p.kobo, 0);
  const valid = total > 0 && parsed.every((p) => !p.problem);
  const pay = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/public/pay/{token}/checkout', {
          params: { path: { token } },
          body: { items: parsed.filter((p) => p.kobo > 0).map((p) => ({ student_id: p.child.student_id, amount_kobo: p.kobo })) },
        }),
      ),
    onSuccess: (checkout) => window.location.assign(checkout.authorization_url),
  });

  if (data.children.length === 0) {
    return (
      <section className="card">
        <p>There are no current pupils on this link. If that's wrong, please contact the school.</p>
      </section>
    );
  }

  return (
    <>
      <h1>School fees</h1>
      {owing.length === 0 && (
        <section className="card">
          <p className="success-text">
            <strong>Everything is paid. Thank you!</strong>
          </p>
        </section>
      )}
      {data.children.map((c) => (
        <ChildCard
          key={c.student_id}
          child={c}
          payByCard={data.card_payments}
          amount={amounts[c.student_id] ?? ''}
          problem={parsed.find((p) => p.child.student_id === c.student_id)?.problem}
          onAmount={(value) => setAmounts({ ...amounts, [c.student_id]: value })}
        />
      ))}
      {data.card_payments && owing.length > 0 && (
        <section className="card" aria-label="Pay by card">
          <ErrorAlert error={pay.error} />
          <div className="row between">
            <span>Total by card</span>
            <strong>{formatKobo(total)}</strong>
          </div>
          <button className="block" onClick={() => pay.mutate()} disabled={!valid || pay.isPending}>
            {pay.isPending ? 'Opening…' : `Pay ${formatKobo(total)} by card`}
          </button>
          <p className="hint">
            Change any amount above to pay part now, or clear it to leave that child for later. You'll pay on the payment provider's
            secure page; each child gets their own receipt.
          </p>
        </section>
      )}
    </>
  );
}

function ChildCard({
  child,
  payByCard,
  amount,
  problem,
  onAmount,
}: {
  child: Child;
  payByCard: boolean;
  amount: string;
  problem?: string;
  onAmount: (value: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="card child-card" aria-label={child.first_name}>
      <div>
        <h2>{child.first_name}</h2>
        <div className="muted small">{child.class_name ?? 'No class this term'}</div>
      </div>
      <div className={`owed ${child.owed_kobo > 0 ? 'due' : 'clear'}`}>
        {child.owed_kobo > 0 ? (
          <>
            <span className="muted small">Owed</span>
            <strong>{formatKobo(child.owed_kobo)}</strong>
          </>
        ) : (
          <strong>All paid</strong>
        )}
        {child.on_plan && child.suggested_kobo < child.owed_kobo && (
          <span className="small">Paying in instalments: {formatKobo(child.suggested_kobo)} is due next</span>
        )}
      </div>
      {child.account_number && child.owed_kobo > 0 && (
        <div className="transfer">
          <div className="muted small">Transfer to {child.bank_name}</div>
          <div className="row between">
            <span className="account-number">{child.account_number}</span>
            <button
              className="secondary small"
              onClick={() =>
                void navigator.clipboard?.writeText(child.account_number ?? '').then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                })
              }
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="hint">This number is for {child.first_name} only. Transfers show up within minutes, with a receipt.</div>
        </div>
      )}
      {payByCard && child.owed_kobo > 0 && (
        <Field label={`Pay by card for ${child.first_name} (₦)`} error={problem}>
          <input inputMode="decimal" value={amount} onChange={(e) => onAmount(e.target.value)} placeholder="0" />
        </Field>
      )}
    </section>
  );
}
