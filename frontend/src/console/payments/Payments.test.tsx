import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { PayComplete } from '../../public/PayComplete';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const bursar = me({
  id: 'bursar-1',
  role: 'bursar',
  capabilities: ['school.view', 'students.view', 'fees.view', 'payments.view', 'payments.record', 'payments.approve', 'payments.reverse'],
});
const term = { id: 't1', session_id: 's1', number: 1, label: 'First term', start_date: '2026-09-07', end_date: '2026-12-11', is_current: true };
const student = { id: 'st1', admission_number: 'A1', full_name: 'Ada Obi' };
const manual = (overrides: Record<string, unknown>) => ({
  id: 'm1', student, amount_kobo: 2_500_000, method: 'cash', received_on: '2026-10-17', reference: '', note: '', status: 'pending',
  recorded_by: 'someone-else', decided_by: null, rejection_reason: '', payment_id: null, created_at: '2026-10-17T10:00:00Z', for_fees: [], ...overrides,
});
const base = {
  'POST /api/v1/auth/refresh': () => json(signedIn(bursar)),
  'GET /api/v1/payments': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
  'GET /api/v1/terms/current': () => json({ ...term, session_name: '2026/2027' }),
};

function renderAt(path: string) {
  return renderApp(
    <Routes>
      <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
      <Route path="/pay/complete" element={<PayComplete />} />
    </Routes>,
    path,
  );
}

describe('payments', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('only lets someone else approve a manual payment', async () => {
    const calls = mockApi({
      ...base,
      'GET /api/v1/manual-payments': () =>
        json({ items: [manual({ id: 'mine', recorded_by: 'bursar-1' }), manual({ id: 'theirs', amount_kobo: 100_000 })], total: 2, limit: 50, offset: 0 }),
      'POST /api/v1/manual-payments/theirs/approve': () => json(manual({ id: 'theirs', status: 'approved' })),
    });
    renderAt('/console/payments');
    await userEvent.click(await screen.findByRole('tab', { name: 'Awaiting approval' }));
    const rows = await screen.findAllByRole('row');
    expect(within(rows[1]!).getByText(/someone else must approve/)).toBeInTheDocument();
    expect(within(rows[1]!).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    await userEvent.click(within(rows[2]!).getByRole('button', { name: 'Approve' }));
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/manual-payments/theirs/approve'))).toBe(true);
  });

  it('marks a transfer for the fee the parent says it was for', async () => {
    const transfer = {
      id: 'p1', student, kind: 'receipt', amount_kobo: 500_000, method: 'virtual_account', gateway: 'fake', gateway_reference: 'FAKE_1',
      reference: '', note: '', received_at: '2026-10-17T10:00:00Z', reversed: false, reverses_payment_id: null,
      applied: [{ invoice_id: 'i1', invoice_number: 7, amount_kobo: 500_000 }], unapplied_kobo: 0, receipt_id: null, receipt_number: null, for_fees: [],
    };
    const calls = mockApi({
      ...base,
      'GET /api/v1/payments': () => json({ items: [transfer], total: 1, limit: 50, offset: 0 }),
      'GET /api/v1/reports/students/st1/fee-breakdown': () =>
        json({
          term_label: '2026/2027 First term', earlier_left_kobo: 0, total_left_kobo: 130_000_00,
          fees: [
            { name: 'PTA levy', billed_kobo: 500_000, paid_kobo: 0, left_kobo: 500_000, status: 'Unpaid' },
            { name: 'Tuition', billed_kobo: 180_000_00, paid_kobo: 50_000_00, left_kobo: 130_000_00, status: 'Part paid' },
          ],
        }),
      'POST /api/v1/payments/p1/fees': () => json({ ...transfer, for_fees: ['PTA levy'] }),
    });
    renderAt('/console/payments');
    await userEvent.click(await screen.findByRole('button', { name: 'Mark for fees' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: /PTA levy/ }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark for PTA levy' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/payments/p1/fees'))).toBe(true));
    const sent = calls.find((c) => c.method === 'POST' && c.url.endsWith('/payments/p1/fees'))!;
    expect(await sent.json()).toEqual({ fees: ['PTA levy'] });
  });

  it('records cash in kobo for the chosen student', async () => {
    const posted: unknown[] = [];
    mockApi({
      ...base,
      'GET /api/v1/manual-payments': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'GET /api/v1/students': () =>
        json({ items: [{ ...student, first_name: 'Ada', last_name: 'Obi', gender: null, status: 'active', placement: null }], total: 1, limit: 8, offset: 0 }),
      'POST /api/v1/manual-payments': async (request) => {
        posted.push(await request.json());
        return json(manual({}), 201);
      },
    });
    renderAt('/console/payments');
    await userEvent.click(await screen.findByRole('button', { name: 'Record cash or teller' }));
    await userEvent.type(screen.getByLabelText('Find student'), 'Ada');
    await userEvent.click(await screen.findByRole('option', { name: /Ada Obi/ }));
    await userEvent.type(screen.getByLabelText('Amount (₦)'), '25,000');
    await userEvent.click(screen.getByRole('button', { name: 'Record' }));
    await screen.findByRole('tab', { name: 'Awaiting approval', selected: true });
    expect(posted).toEqual([expect.objectContaining({ student_id: 'st1', amount_kobo: 2_500_000, method: 'cash' })]);
  });

  it('shows the confirmed result after a card payment', async () => {
    mockApi({ 'POST /api/v1/payments/verify': () => json({ reference: 'SP0123456789ABCDEF0123', status: 'succeeded', amount_kobo: 15_500_000 }) });
    renderAt('/pay/complete?reference=SP0123456789ABCDEF0123');
    expect(await screen.findByText(/Your payment of .*155,000\.00 was received/)).toBeInTheDocument();
  });

  it('refuses a malformed reference without calling the API', async () => {
    const calls = mockApi({});
    renderAt('/pay/complete?reference=nonsense');
    expect(await screen.findByText(/link is incomplete/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it('shows what Paystack paid into the bank and what is missing', async () => {
    const payout = {
      id: 'p1',
      settled_on: '2026-10-20',
      status: 'success',
      gross_kobo: 6_500_000,
      charges_kobo: 90_000,
      paid_out_kobo: 6_410_000,
      transactions: 2,
      matched: 1,
    };
    const calls = mockApi({
      ...base,
      'POST /api/v1/auth/refresh': () => json(signedIn({ ...bursar, capabilities: [...bursar.capabilities, 'reports.view'] })),
      'GET /api/v1/manual-payments': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'GET /api/v1/settlements': () =>
        json({
          start: '2026-09-25',
          end: '2026-10-24',
          connected: true,
          checked_at: '2026-10-24T07:20:00Z',
          gross_kobo: 6_500_000,
          charges_kobo: 90_000,
          paid_out_kobo: 6_410_000,
          payouts: [payout],
          unsettled: [
            {
              payment_id: 'pay2',
              received_at: '2026-10-18T09:00:00Z',
              receipt_number: 41,
              admission_number: 'A1',
              method: 'virtual_account',
              amount_kobo: 4_000_000,
              reference: 'PSK_REF_2',
            },
          ],
          unsettled_kobo: 4_000_000,
          unknown: [{ settled_on: '2026-10-20', reference: 'NOT_OURS_1', amount_kobo: 500_000 }],
        }),
      'POST /api/v1/settlements/refresh': () => json({ status: 'queued' }, 202),
    });
    renderAt('/console/payments');
    await userEvent.click(await screen.findByRole('tab', { name: 'Paid into the bank' }));
    const payouts = within(await screen.findByRole('region', { name: 'Payouts' }));
    expect(payouts.getByText('₦64,100.00')).toBeInTheDocument();
    expect(payouts.getByText('1 of 2')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Not paid out yet' })).getByText('PSK_REF_2')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Paid out but not in WardPay' })).getByText('NOT_OURS_1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/settlements/refresh'))).toBe(true));
  });
});
