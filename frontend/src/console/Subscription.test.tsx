import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../api/client';
import { AuthProvider } from '../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { ConsoleApp } from './ConsoleApp';

const REFERENCE = 'WS' + 'A1'.repeat(10);
const plan = {
  id: 'p1',
  name: 'Standard',
  description: '',
  setup_fee_kobo: 5_000_000,
  per_pupil_year_kobo: 150_000,
  per_pupil_term_kobo: 50_000,
  is_default: true,
  is_active: true,
  schools: 0,
};
const invoice = (status: string, balance: number) => ({
  id: 'i1',
  number: 7,
  school_id: 's1',
  school_name: 'Grace Model College',
  plan_name: 'Standard',
  period: '2026/2027 First term',
  pupils: 100,
  issued_at: '2026-10-01T09:00:00Z',
  due_date: '2026-10-31',
  total_kobo: 10_000_000,
  paid_kobo: 10_000_000 - balance,
  balance_kobo: balance,
  status,
});

function renderPage(path: string, capabilities: string[], invoiceStatus = 'unpaid', balance = 10_000_000) {
  const calls = mockApi({
    'POST /api/v1/auth/refresh': () => json(signedIn(me({ capabilities }))),
    'GET /api/v1/subscription': () =>
      json({ plan, pupils_this_term: 100, next_term_estimate_kobo: 5_000_000, pay_to: null, invoices: [invoice(invoiceStatus, balance)] }),
    'POST /api/v1/subscription/invoices/i1/checkout': () =>
      json({ reference: REFERENCE, amount_kobo: balance, authorization_url: 'https://checkout.example/pay' }, 201),
    'POST /api/v1/subscription/checkouts/verify': () =>
      json({ reference: REFERENCE, status: 'succeeded', amount_kobo: 10_000_000, review_reason: '' }),
  });
  renderApp(
    <Routes>
      <Route
        path="/console/*"
        element={
          <AuthProvider>
            <ConsoleApp />
          </AuthProvider>
        }
      />
    </Routes>,
    path,
  );
  return calls;
}

describe('subscription page', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setAccessToken(null);
  });

  it('opens the card payment page for an unpaid bill, with no licence fee anywhere', async () => {
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign } as Location);
    const calls = renderPage('/console/subscription', ['reports.view', 'payments.record']);
    await userEvent.click(await screen.findByRole('button', { name: 'Pay by card' }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('https://checkout.example/pay'));
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/subscription/invoices/i1/checkout'))).toBe(true);
    expect(screen.queryByText('Licence')).not.toBeInTheDocument();
  });

  it('shows no card button without payments.record, or on a paid bill', async () => {
    renderPage('/console/subscription', ['reports.view']);
    expect(await screen.findByText('2026/2027 First term')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pay by card' })).not.toBeInTheDocument();
  });

  it('confirms the payment with the server when the school comes back from the gateway', async () => {
    const calls = renderPage(
      `/console/subscription?reference=${REFERENCE}&trxref=${REFERENCE}`,
      ['reports.view', 'payments.record'],
      'paid',
      0,
    );
    expect(await screen.findByText(/Thank you. We received ₦100,000.00/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/subscription/checkouts/verify'))).toBe(true);
    expect(screen.queryByRole('button', { name: 'Pay by card' })).not.toBeInTheDocument();
  });
});
