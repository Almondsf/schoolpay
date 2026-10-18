import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';

import { api, ApiError, unwrap } from '../api/client';
import { formatDate, formatKobo } from '../lib/format';
import { Loading } from '../lib/ui';

/**
 * Public receipt check, reached by scanning a receipt's QR code. It shows only
 * what's needed to tell a genuine receipt from a fake: school, number, amount,
 * date, status and the student's initials.
 */
export function VerifyReceipt() {
  const { code = '' } = useParams();
  const valid = /^[A-Za-z0-9_-]{16,32}$/.test(code);
  const receipt = useQuery({
    queryKey: ['verify-receipt', code],
    enabled: valid,
    retry: false,
    queryFn: async () => unwrap(await api.GET('/api/v1/public/receipts/{code}', { params: { path: { code } } })),
  });
  const notFound = !valid || (receipt.error instanceof ApiError && receipt.error.status === 404);

  return (
    <main className="page page--narrow">
      <div className="card verify">
        <h1>Receipt check</h1>
        {notFound ? (
          <div className="verdict bad">
            <strong>Not genuine</strong>
            <p>No receipt matches this code. Don't accept it as proof of payment; ask the school.</p>
          </div>
        ) : receipt.isPending ? (
          <Loading />
        ) : receipt.isError ? (
          <p>We couldn't check this receipt right now. Please try again shortly.</p>
        ) : (
          <>
            <div className={`verdict ${receipt.data.status === 'valid' ? 'good' : 'bad'}`}>
              <strong>{receipt.data.status === 'valid' ? 'Genuine receipt' : 'This payment was reversed'}</strong>
              {receipt.data.status !== 'valid' && <p>The school cancelled this payment. The receipt is no longer valid.</p>}
            </div>
            <dl className="details">
              <dt>School</dt>
              <dd>{receipt.data.school_name}</dd>
              <dt>Receipt no.</dt>
              <dd>{receipt.data.receipt_number}</dd>
              <dt>Amount</dt>
              <dd>{formatKobo(receipt.data.amount_kobo)}</dd>
              <dt>Paid on</dt>
              <dd>{formatDate(receipt.data.paid_on)}</dd>
              <dt>Student</dt>
              <dd>{receipt.data.student_initials}</dd>
            </dl>
            <p className="muted small">Check that these details match the printed receipt.</p>
          </>
        )}
      </div>
    </main>
  );
}
