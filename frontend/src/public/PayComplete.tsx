import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import { formatKobo } from '../lib/format';
import { ErrorAlert, Loading } from '../lib/ui';

/**
 * Where the payment gateway sends parents after a card payment. The result shown
 * comes from the gateway (via our API), never from the URL.
 */
export function PayComplete() {
  const [params] = useSearchParams();
  // Paystack sends ?reference= (and trxref=); Flutterwave sends ?tx_ref=
  const reference = params.get('reference') ?? params.get('trxref') ?? params.get('tx_ref') ?? '';
  const result = useQuery({
    queryKey: ['pay-complete', reference],
    enabled: /^SP[0-9A-F]{20}$/.test(reference),
    retry: false,
    // A payment can take a few seconds to confirm: check again while it's pending
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
    queryFn: async () => unwrap(await api.POST('/api/v1/payments/verify', { body: { reference } })),
  });

  return (
    <main className="page page--narrow">
      <div className="card">
        <h1>Payment</h1>
        {!/^SP[0-9A-F]{20}$/.test(reference) ? (
          <p>This link is incomplete. If you paid, your school will see it shortly.</p>
        ) : result.isPending ? (
          <Loading />
        ) : result.isError ? (
          <ErrorAlert error={result.error} />
        ) : result.data.status === 'succeeded' ? (
          <>
            <p className="success-text">
              <strong>Thank you. Your payment of {formatKobo(result.data.amount_kobo)} was received.</strong>
            </p>
            <p>The school has been notified and a receipt will follow.</p>
          </>
        ) : result.data.status === 'pending' ? (
          <p>We're confirming your payment with the bank. This page updates by itself.</p>
        ) : result.data.status === 'failed' ? (
          <p>The payment didn't go through and you haven't been charged. You can try again from the link the school sent.</p>
        ) : (
          <p>Your payment was received but needs a quick check by the school. They'll be in touch if anything is needed.</p>
        )}
        <p className="muted small">Reference: {reference || '—'}</p>
      </div>
    </main>
  );
}
