import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { Signup } from '../../public/Signup';
import { ConsoleApp } from '../ConsoleApp';

const platformAdmin = me({ role: 'platform_admin', school: null, capabilities: ['platform.manage'], mfa_enabled: true });
const invoice = {
  id: 'i1', number: 7, school_id: 's1', school_name: 'Alpha College', plan_name: 'Standard', period: '2026/2027 First term',
  pupils: 3, issued_at: '2026-10-01T09:00:00Z', due_date: '2026-10-31', total_kobo: 16_500_000, paid_kobo: 0,
  balance_kobo: 16_500_000, status: 'unpaid',
};
const run = (dry: boolean) => ({
  dry_run: dry, total_kobo: 16_500_000, already_billed: 1, no_current_term: ['Gamma School'],
  bills: [{ school_id: 's1', school_name: 'Alpha College', plan_name: 'Standard', period: '2026/2027 First term', pupils: 3,
    total_kobo: 16_500_000, lines: [{ kind: 'setup', description: 'Set-up fee (once)', quantity: 1, unit_kobo: 5_000_000, amount_kobo: 5_000_000 }] }],
});

function renderConsole(path: string) {
  return renderApp(
    <Routes>
      <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
    </Routes>,
    path,
  );
}

describe('platform console', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('bills the term after a preview, and records a transfer', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
      'GET /api/v1/platform/billing/invoices': () => json({ items: [invoice], total: 1, limit: 50, offset: 0 }),
      'POST /api/v1/platform/billing/generate': async (request) => json(run(((await request.json()) as { dry_run: boolean }).dry_run)),
      'POST /api/v1/platform/billing/invoices/i1/payments': async (request) =>
        json({ invoice: { ...invoice, ...((await request.json()) as object) }, lines: [], payments: [] }),
    });
    renderConsole('/console/platform/billing');
    expect(await screen.findByRole('link', { name: 'Alpha College' }, { timeout: 5000 })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Bill this term' }));
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/Preview: 1 bills totalling ₦165,000.00/)).toBeInTheDocument();
    expect(screen.getByText(/1 schools were already billed/)).toBeInTheDocument();
    expect(screen.getByText(/not billed: Gamma School/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Send 1 bills' }));
    expect(await screen.findByText(/Done: 1 bills/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));

    await userEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const dialog = screen.getByRole('dialog');
    const amount = within(dialog).getByLabelText('Amount (₦)');
    await userEvent.clear(amount);
    await userEvent.type(amount, '50,000');
    await userEvent.type(within(dialog).getByLabelText('Bank reference (optional)'), 'NIP/77');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record payment' }));
    await vi.waitFor(async () => {
      const paid = calls.find((c) => c.url.endsWith('/payments'))!;
      expect(await paid.clone().json()).toMatchObject({ amount_kobo: 5_000_000, reference: 'NIP/77' });
    });
  });

  it('switches the payment provider', async () => {
    let active = 'paystack';
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
      'GET /api/v1/platform/providers': () =>
        json([{ service: 'payment', active_provider: active, available: ['paystack', 'bachs'], updated_at: null }]),
      'PUT /api/v1/platform/providers/payment': async (request) => {
        active = (await request.json()).provider;
        return json({ service: 'payment', active_provider: active, available: ['paystack', 'bachs'], updated_at: '2026-10-20T10:00:00Z' });
      },
    });
    renderConsole('/console/platform/providers');
    const paystack = await screen.findByRole('radio', { name: /Paystack/ });
    expect(paystack).toBeChecked();
    expect(screen.getByRole('button', { name: 'Switch' })).toBeDisabled();
    await userEvent.click(screen.getByRole('radio', { name: /Bachs/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Switch' }));
    expect(await screen.findByText('Switched to Bachs.')).toBeInTheDocument();
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(await put.json()).toEqual({ provider: 'bachs' });
  });

  it('platform admins land on the overview', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
      'GET /api/v1/platform/overview': () =>
        json({
          schools_active: 4, schools_pending: 1, schools_suspended: 0, signups_30_days: 2, pupils: 240,
          processed_this_month_kobo: 100_000, processed_last_month_kobo: 0, payments_this_month: 3,
          subscription_billed_kobo: 0, subscription_paid_kobo: 0, subscription_outstanding_kobo: 0, subscription_overdue_kobo: 0,
        }),
    });
    renderConsole('/console');
    expect(await screen.findByRole('heading', { name: 'Platform overview' }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Signed up, email not confirmed' })).toHaveAttribute('href', '/console/platform/schools?status=pending');
  });
});

describe('school sign-up', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends the form and asks the proprietor to check their email', async () => {
    const calls = mockApi({ 'POST /api/v1/public/signup': () => json({ detail: 'sent' }, 202) });
    renderApp(<Signup />);
    await userEvent.type(screen.getByLabelText('School name'), 'Little Angels Academy');
    await userEvent.type(screen.getByLabelText('Your name (proprietor)'), 'Mrs Funke Bello');
    await userEvent.type(screen.getByLabelText(/^Your email/), 'funke@example.com');
    await userEvent.type(screen.getByLabelText('Phone'), '0803 123 4567');
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Sign up' }));
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument();
    const body = (await calls[0]!.json()) as Record<string, unknown>;
    expect(body).toMatchObject({ school_name: 'Little Angels Academy', accept_terms: true, website: '' });
  });
});
