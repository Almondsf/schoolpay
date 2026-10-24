import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { lagosToday } from '../../lib/format';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const today = lagosToday();
const pending = {
  id: 'e1',
  category_id: 'c1',
  category: 'Diesel and fuel',
  amount_kobo: 2_500_000,
  spent_on: today,
  payee: 'Total filling station',
  description: 'Generator diesel',
  paid_from: 'cash',
  reference: 'RCPT-12',
  status: 'pending',
  recorded_by: 'u2',
  rejection_reason: '',
  void_reason: '',
  created_at: `${today}T10:00:00Z`,
};
const report = {
  start: today,
  end: today,
  income: [{ label: 'Cash', amount_kobo: 10_000_000, count: 4 }],
  expenditure: [{ label: 'Salaries and wages', amount_kobo: 12_000_000, count: 2 }],
  income_kobo: 10_000_000,
  expenditure_kobo: 12_000_000,
  surplus_kobo: -2_000_000,
  awaiting_approval_kobo: 2_500_000,
  billed_kobo: 50_000_000,
};

function renderExpenses(capabilities: string[]) {
  const calls = mockApi({
    'POST /api/v1/auth/refresh': () => json(signedIn(me({ capabilities }))),
    'GET /api/v1/expenses': () => json({ items: [pending], total: 1, limit: 50, offset: 0 }),
    'GET /api/v1/expense-categories': () => json([{ id: 'c1', name: 'Diesel and fuel', is_active: true }]),
    'POST /api/v1/expenses': async (request) => json({ ...pending, ...(await request.json()), id: 'e2' }, 201),
    'POST /api/v1/expenses/e1/approve': () => json({ ...pending, status: 'approved' }),
    'GET /api/v1/reports/income-expenditure': () => json(report),
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
    '/console/expenses',
  );
  return calls;
}

describe('expenses', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('records spending and approves another person’s', async () => {
    const calls = renderExpenses(['payments.view', 'payments.record', 'payments.approve', 'reports.view']);
    const table = within(await screen.findByRole('table', { name: 'Spending' }));
    expect(table.getByText('Awaiting approval')).toBeInTheDocument();
    await userEvent.click(table.getByRole('button', { name: 'Approve' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/expenses/e1/approve'))).toBe(true));

    await userEvent.click(screen.getByRole('button', { name: 'Record spending' }));
    const dialog = within(screen.getByRole('dialog'));
    await userEvent.selectOptions(await dialog.findByLabelText('Category'), 'c1');
    await userEvent.type(dialog.getByLabelText('Amount (₦)'), '25000');
    await userEvent.type(dialog.getByLabelText('Paid to'), 'Total filling station');
    expect(dialog.getByText(/comes off the cash that should be in the drawer/)).toBeInTheDocument();
    await userEvent.click(dialog.getByRole('button', { name: 'Record spending' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/api/v1/expenses'))).toBe(true));
    const sent = await calls.find((c) => c.method === 'POST' && c.url.endsWith('/api/v1/expenses'))!.json();
    expect(sent).toMatchObject({
      category_id: 'c1',
      amount_kobo: 2_500_000,
      payee: 'Total filling station',
      paid_from: 'cash',
      spent_on: today,
    });
  });

  it('shows income against expenditure, with a deficit', async () => {
    renderExpenses(['payments.view', 'reports.view']);
    await userEvent.click(await screen.findByRole('tab', { name: 'Income and expenditure' }));
    expect(await screen.findByText('Deficit')).toBeInTheDocument();
    expect(screen.getByText('₦20,000.00')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Money out' })).getByText(/Salaries and wages/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Record spending' })).not.toBeInTheDocument(); // no payments.record
  });
});
