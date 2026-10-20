import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { BRAND } from '../../lib/brand';
import { ErrorAlert, Field, Loading, Notice, PageHeader } from '../../lib/ui';

type Gateway = Schemas['GatewayCode'];
export const GATEWAY_NAMES: Record<Gateway, string> = {
  paystack: 'Paystack',
  bachs: 'Bachs',
  flutterwave: 'Flutterwave',
  fake: 'Test gateway (no real money)',
};

export function PaymentSettings() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['payment-settings'], queryFn: async () => unwrap(await api.GET('/api/v1/payment-settings')) });
  const provision = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/virtual-accounts/provision', { body: {} })),
  });
  const [editing, setEditing] = useState(false);
  const bachs = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/payment-settings/bachs/onboarding')),
    onSuccess: (link) => window.location.assign(link.url),
  });

  if (settings.isPending) return <Loading />;
  if (settings.isError) return <ErrorAlert error={settings.error} />;
  const account = settings.data.account;
  const onBachs = settings.data.accounts.some((a) => a.gateway === 'bachs');
  const bankGateways = settings.data.gateways.filter((g) => g !== 'bachs');

  return (
    <>
      <PageHeader
        title="Payment settings"
        subtitle={`Parents' payments settle straight into the school's own bank account. ${BRAND} never holds the money.`}
      />
      <Notice>
        Payments currently go through <strong>{GATEWAY_NAMES[settings.data.active_provider]}</strong>
        {account && account.gateway !== settings.data.active_provider
          ? `. This school isn't set up on it yet, so its payments still go through ${GATEWAY_NAMES[account.gateway]}.`
          : '.'}
      </Notice>
      <div className="grid-2">
        <div className="card">
          <h2>School bank account</h2>
          {account && !editing ? (
            <>
              <dl className="details">
                <dt>Bank</dt>
                <dd>{account.bank_name}</dd>
                <dt>Account</dt>
                <dd>
                  {account.account_name} (****{account.account_number_last4})
                </dd>
                <dt>Provider</dt>
                <dd>{GATEWAY_NAMES[account.gateway]}</dd>
                <dt>Platform fee</dt>
                <dd>{account.platform_fee_bp / 100}% of each payment</dd>
              </dl>
              {settings.data.accounts.length > 1 && (
                <p className="small muted">
                  Also set up on: {settings.data.accounts.filter((a) => a.gateway !== account.gateway).map((a) => GATEWAY_NAMES[a.gateway]).join(', ')}
                </p>
              )}
              {can('payments.setup') && (
                <div className="row wrap">
                  {bankGateways.length > 0 && (
                    <button className="secondary" onClick={() => setEditing(true)}>
                      Connect a bank account
                    </button>
                  )}
                  {settings.data.gateways.includes('bachs') && (
                    <button className="secondary" onClick={() => bachs.mutate()} disabled={bachs.isPending}>
                      {onBachs ? 'Finish setting up with Bachs' : 'Set up with Bachs'}
                    </button>
                  )}
                </div>
              )}
              <ErrorAlert error={bachs.error} />
            </>
          ) : can('payments.setup') ? (
            <>
              {settings.data.gateways.includes('bachs') && (
                <p>
                  <button onClick={() => bachs.mutate()} disabled={bachs.isPending}>
                    Set up with Bachs
                  </button>{' '}
                  <span className="small muted">Bachs takes the school's details and bank account on its own pages.</span>
                </p>
              )}
              <ErrorAlert error={bachs.error} />
              {bankGateways.length > 0 && <ConnectAccount gateways={bankGateways} onDone={() => {
              setEditing(false);
              void queryClient.invalidateQueries({ queryKey: ['payment-settings'] });
            }} />}
            </>
          ) : (
            <p className="muted">Not set up yet. The proprietor connects the school's bank account here.</p>
          )}
        </div>
        <div className="card">
          <h2>Students' account numbers</h2>
          <p>
            Each student gets their own bank account number. Any transfer into it is matched to that student automatically, so parents can pay
            from any bank app.
          </p>
          <ErrorAlert error={provision.error} />
          {provision.data && <Notice>Requested account numbers for {provision.data.requested} students. They appear on each student's page shortly.</Notice>}
          {can('payments.approve') && (
            <button onClick={() => provision.mutate()} disabled={!account || provision.isPending}>
              Issue account numbers to students without one
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function ConnectAccount({ gateways, onDone }: { gateways: Gateway[]; onDone: () => void }) {
  const [gateway, setGateway] = useState<Gateway>(gateways[0] ?? 'paystack');
  const [bankCode, setBankCode] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const banks = useQuery({
    queryKey: ['banks', gateway],
    queryFn: async () => unwrap(await api.GET('/api/v1/payment-settings/banks', { params: { query: { gateway } } })),
  });
  const save = useMutation({
    mutationFn: async () => unwrap(await api.PUT('/api/v1/payment-settings', { body: { gateway, bank_code: bankCode, account_number: accountNumber } })),
    onSuccess: onDone,
  });
  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <ErrorAlert error={save.error} />
      <Field label="Payment provider">
        <select value={gateway} onChange={(e) => setGateway(e.target.value as Gateway)}>
          {gateways.map((g) => (
            <option key={g} value={g}>
              {GATEWAY_NAMES[g]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Bank">
        <select value={bankCode} onChange={(e) => setBankCode(e.target.value)} required>
          <option value="">{banks.isPending ? 'Loading banks…' : 'Choose…'}</option>
          {(banks.data ?? []).map((b) => (
            <option key={b.code} value={b.code}>
              {b.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Account number" hint="The school's own account. The provider confirms the account name.">
        <input value={accountNumber} onChange={(e) => setAccountNumber(e.target.value.replace(/\D/g, ''))} inputMode="numeric" pattern="\d{10}" maxLength={10} required />
      </Field>
      <button type="submit" disabled={save.isPending}>
        {save.isPending ? 'Checking with the bank…' : 'Connect account'}
      </button>
    </form>
  );
}
