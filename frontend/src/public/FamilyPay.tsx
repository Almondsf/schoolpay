import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { BRAND } from '../lib/brand';
import { formatKobo, koboToInput, parseNairaToKobo } from '../lib/format';
import { FeeList, FeePicker, leftOn } from '../lib/FeeBreakdown';
import { ErrorAlert, Field, Loading } from '../lib/ui';

type Child = Schemas['FamilyChildOut'];
type Part = Schemas['FamilyPartIn'];
type Checkout = (items: Part[]) => Promise<Schemas['FamilyCheckoutOut']>;

/** A parent's family payment page, opened from the link in their bills and reminders (no sign-in). */
export function FamilyPay() {
  const { token = '' } = useParams();
  const page = useQuery({
    queryKey: ['family-pay', token],
    staleTime: 0,
    retry: false,
    queryFn: async () => unwrap(await api.GET('/api/v1/public/pay/{token}', { params: { path: { token } } })),
  });
  const checkout: Checkout = async (items) =>
    unwrap(await api.POST('/api/v1/public/pay/{token}/checkout', { params: { path: { token } }, body: { items } }));

  return (
    <div className="parent-shell">
      <header className="parent-bar">
        <strong>{page.data?.school_name ?? BRAND}</strong>
      </header>
      <main className="parent-page">
        {page.isPending ? <Loading /> : page.isError ? <ErrorAlert error={page.error} /> : <FamilyPayment data={page.data} checkout={checkout} />}
      </main>
    </div>
  );
}

/**
 * Paying for all of a parent's children at once (the family link, and "Pay for all your
 * children" in the portal). Each child's account number for transfers, and one online
 * payment (transfer, card or USSD on the provider's page) with the amount chosen per child.
 */
export function FamilyPayment({ data, checkout }: { data: Schemas['FamilyPageOut']; checkout: Checkout }) {
  const owing = data.children.filter((c) => c.owed_kobo > 0);
  // What the parent will pay per child, as typed (prefilled with what's due now)
  const [amounts, setAmounts] = useState<Record<string, string>>(() =>
    Object.fromEntries(owing.map((c) => [c.student_id, koboToInput(c.suggested_kobo)])),
  );
  // Fees the parent ticked per child (e.g. only WAEC); none: the amount goes to the bill in its usual order
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const pick = (child: Child, names: string[]) => {
    setPicked({ ...picked, [child.student_id]: names });
    setAmounts({ ...amounts, [child.student_id]: koboToInput(names.length ? leftOn(child.fees, names) : child.suggested_kobo) });
  };
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
  const paying = parsed.filter((p) => p.kobo > 0).length;
  const pay = useMutation({
    mutationFn: () =>
      checkout(
        parsed
          .filter((p) => p.kobo > 0)
          .map((p) => ({ student_id: p.child.student_id, amount_kobo: p.kobo, fees: picked[p.child.student_id] ?? [] })),
      ),
    onSuccess: (started) => window.location.assign(started.authorization_url),
  });

  if (data.children.length === 0) {
    return (
      <section className="card">
        <p>There are no current pupils here. If that's wrong, please contact the school.</p>
      </section>
    );
  }

  const payNow = data.card_payments && owing.length > 0 && (
    <>
      <ErrorAlert error={pay.error} />
      <button className="block" onClick={() => pay.mutate()} disabled={!valid || pay.isPending}>
        {pay.isPending ? 'Opening…' : `Pay ${formatKobo(total)} now`}
      </button>
      <p className="hint">
        For {paying} {paying === 1 ? 'child' : 'children'}. On the next page, pay by <strong>bank transfer</strong>, card or USSD. Each
        child gets their own receipt.
      </p>
    </>
  );

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
          payOnline={data.card_payments}
          amount={amounts[c.student_id] ?? ''}
          problem={parsed.find((p) => p.child.student_id === c.student_id)?.problem}
          onAmount={(value) => setAmounts({ ...amounts, [c.student_id]: value })}
          picked={picked[c.student_id] ?? []}
          onPick={(names) => pick(c, names)}
        />
      ))}
      {payNow && (
        <section className="card" aria-label="Pay for all your children">
          <div className="row between">
            <span>Due now for {owing.length === 1 ? 'your child' : `all ${owing.length} children`}</span>
            <strong>{formatKobo(total)}</strong>
          </div>
          {owing.length > 1 && <p className="hint">Change any amount above to pay part now, or clear it to leave that child for later.</p>}
          {payNow}
        </section>
      )}
    </>
  );
}

function ChildCard({
  child,
  payOnline,
  amount,
  problem,
  onAmount,
  picked,
  onPick,
}: {
  child: Child;
  payOnline: boolean;
  amount: string;
  problem?: string;
  onAmount: (value: string) => void;
  picked: string[];
  onPick: (names: string[]) => void;
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
      <FeeList fees={child.fees} earlierKobo={child.earlier_left_kobo} />
      {payOnline && child.owed_kobo > 0 && child.fees.some((f) => f.left_kobo > 0) && (
        <FeePicker fees={child.fees} picked={picked} onChange={onPick} label="Pay for particular fees? (optional)" />
      )}
      {payOnline && child.owed_kobo > 0 && (
        <Field
          label={`Amount for ${child.first_name} now (₦)`}
          hint={picked.length ? `For ${picked.join(', ')}` : undefined}
          error={problem}
        >
          <input inputMode="decimal" value={amount} onChange={(e) => onAmount(e.target.value)} placeholder="0" />
        </Field>
      )}
      {child.account_number && child.owed_kobo > 0 && (
        <div className="transfer">
          <div className="muted small">Or transfer any time to {child.first_name}'s own account</div>
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
          <div className="hint">
            {child.bank_name}. This number is for {child.first_name} only and never changes. Any amount, any time, with a receipt.
          </div>
        </div>
      )}
    </section>
  );
}
