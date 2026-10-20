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
});
