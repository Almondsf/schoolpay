import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const platformAdmin = me({ role: 'platform_admin', school: null, capabilities: ['platform.manage'], mfa_enabled: true });
const invoice = {
  id: 'i1',
  number: 7,
  school_id: 's1',
  school_name: 'Alpha College',
  plan_name: 'Standard',
  period: '2026/2027 First term',
  pupils: 3,
  issued_at: '2026-10-01T09:00:00Z',
  due_date: '2026-10-31',
  total_kobo: 6_500_000,
  paid_kobo: 0,
  balance_kobo: 6_500_000,
  status: 'unpaid',
};
const line = (kind: string, amount: number) => ({ kind, description: kind, quantity: 1, unit_kobo: amount, amount_kobo: amount });

function renderBilling() {
  const calls = mockApi({
    'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
    'GET /api/v1/platform/billing/invoices': () => json({ items: [invoice], total: 1, limit: 50, offset: 0 }),
    'GET /api/v1/platform/billing/invoices/i1': () =>
      json({ invoice, lines: [line('setup', 5_000_000), line('pupils', 1_500_000)], payments: [] }),
    'POST /api/v1/platform/billing/invoices/i1/discount': () => json({ invoice, lines: [], payments: [] }),
    'PUT /api/v1/platform/billing/invoices/i1/due-date': () => json({ invoice, lines: [], payments: [] }),
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
    '/console/platform/billing',
  );
  return calls;
}

describe('adjusting a school’s bill', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('waives the set-up fee in one click', async () => {
    const calls = renderBilling();
    await userEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    await userEvent.click(await screen.findByRole('button', { name: /Waive the set-up fee \(₦50,000.00\)/ }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/i1/discount'))).toBe(true));
    const sent = calls.find((c) => c.url.endsWith('/i1/discount'))!;
    expect(await sent.json()).toEqual({ amount_kobo: 5_000_000, reason: 'Set-up fee waived' });
  });

  it('gives a discount with a reason, and moves the due date', async () => {
    const calls = renderBilling();
    await userEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(dialog.getByLabelText('Discount (₦)'), '10000');
    await userEvent.type(dialog.getByLabelText('Reason (shown on the bill)'), 'First-year discount');
    await userEvent.click(dialog.getByRole('button', { name: 'Take it off' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/i1/discount'))).toBe(true));
    expect(await calls.find((c) => c.url.endsWith('/i1/discount'))!.json()).toEqual({
      amount_kobo: 1_000_000,
      reason: 'First-year discount',
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    const again = within(await screen.findByRole('dialog'));
    await userEvent.clear(again.getByLabelText('Due on'));
    await userEvent.type(again.getByLabelText('Due on'), '2026-11-30');
    await userEvent.type(again.getByLabelText('Reason (kept in the activity log)'), 'Asked for more time');
    await userEvent.click(again.getByRole('button', { name: 'Change due date' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.endsWith('/i1/due-date'))).toBe(true));
    expect(await calls.find((c) => c.url.endsWith('/i1/due-date'))!.json()).toEqual({
      due_date: '2026-11-30',
      reason: 'Asked for more time',
    });
  });
});
