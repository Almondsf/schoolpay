import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { formatDateTime } from '../../lib/format';
import { ErrorAlert, Loading, Notice, PageHeader } from '../../lib/ui';
import { GATEWAY_NAMES } from '../payments/PaymentSettings';

type Gateway = Schemas['GatewayCode'];

/** Which payment provider new checkouts go through, switched without a deploy. */
export function PlatformProviders() {
  const queryClient = useQueryClient();
  const providers = useQuery({
    queryKey: ['platform-providers'],
    queryFn: async () => unwrap(await api.GET('/api/v1/platform/providers')),
  });
  const payment = providers.data?.find((p) => p.service === 'payment');
  const [choice, setChoice] = useState<Gateway | ''>('');
  useEffect(() => setChoice(payment?.active_provider ?? ''), [payment?.active_provider]);
  const save = useMutation({
    mutationFn: async (provider: Gateway) => unwrap(await api.PUT('/api/v1/platform/providers/payment', { body: { provider } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['platform-providers'] }),
  });

  if (providers.isPending) return <Loading />;
  if (providers.isError || !payment) return <ErrorAlert error={providers.error} />;
  return (
    <>
      <PageHeader title="Providers" subtitle="Switch providers at once, without a new release." />
      <section className="card section page--narrow" aria-label="Payment provider">
        <h2>Payment provider</h2>
        <p className="small">
          New card and bank-transfer checkouts go through this provider. Payments already started finish where they began, and
          transfers into students’ own account numbers keep arriving from the provider that issued them. A school not yet set up on the
          chosen provider keeps using the one it has.
        </p>
        <fieldset>
          <legend>Use</legend>
          {payment.available.map((g) => (
            <label key={g} className="checkbox">
              <input type="radio" name="payment-provider" value={g} checked={choice === g} onChange={() => setChoice(g)} />
              {GATEWAY_NAMES[g]}
              {g === payment.active_provider && <span className="badge ok">In use</span>}
            </label>
          ))}
        </fieldset>
        <ErrorAlert error={save.error} />
        {save.isSuccess && <Notice>Switched to {GATEWAY_NAMES[save.data.active_provider]}.</Notice>}
        <div className="row">
          <button onClick={() => choice && save.mutate(choice)} disabled={!choice || choice === payment.active_provider || save.isPending}>
            {save.isPending ? 'Switching…' : 'Switch'}
          </button>
        </div>
        {payment.updated_at && <p className="muted small">Last changed {formatDateTime(payment.updated_at)}</p>}
        <p className="hint">Only providers whose keys are configured on the server are listed.</p>
      </section>
    </>
  );
}
