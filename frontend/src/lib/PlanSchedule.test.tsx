import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../api/client';
import { AuthProvider } from '../auth/AuthContext';
import { ConsoleApp } from '../console/ConsoleApp';
import { json, me, mockApi, signedIn } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { splitEvenly, stepDates } from './PlanSchedule';

describe('instalment helpers', () => {
  it('splits evenly in whole naira, the last instalment taking what is left', () => {
    expect(splitEvenly(15_500_000, 3)).toEqual([5_166_600, 5_166_600, 5_166_800]);
    expect(splitEvenly(15_500_000, 3).reduce((a, b) => a + b)).toBe(15_500_000);
    expect(splitEvenly(10_000, 2)).toEqual([5_000, 5_000]);
  });

  it('steps dates by month (keeping the day, capped at month end), fortnight or week', () => {
    expect(stepDates('2026-01-31', 3, 'month')).toEqual(['2026-01-31', '2026-02-28', '2026-03-31']);
    expect(stepDates('2026-10-20', 3, 'fortnight')).toEqual(['2026-10-20', '2026-11-03', '2026-11-17']);
    expect(stepDates('2026-12-28', 2, 'week')).toEqual(['2026-12-28', '2027-01-04']);
  });
});

const bursar = me({ role: 'bursar', capabilities: ['fees.view', 'invoices.issue', 'students.view'] });
const invoice = (plan: unknown = null) => ({
  id: 'inv1', number: 12, kind: 'term', term_id: 't1', student: { id: 's1', admission_number: 'DMC/1', full_name: 'Tolu Adeyemi' },
  issued_at: '2026-09-07T08:00:00Z', due_date: '2026-09-30', total_kobo: 15_500_000, paid_kobo: 0, balance_kobo: 15_500_000,
  status: 'unpaid', void_reason: null, lines: [], plan,
});

describe('payment plan on a bill', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('agrees a plan from an even split and shows the schedule', async () => {
    let sent: { instalments: { due_date: string; amount_kobo: number }[] } | undefined;
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(bursar)),
      'GET /api/v1/invoices/inv1': () => json(invoice()),
      'POST /api/v1/invoices/inv1/plan': async (request) => {
        sent = (await request.json()) as typeof sent;
        return json(
          invoice({
            id: 'p1', note: '', created_at: '2026-10-19T09:00:00Z', covered_kobo: 15_500_000, paid_on_plan_kobo: 0,
            behind_kobo: 0, next_due: sent!.instalments[0]!.due_date, next_amount_kobo: sent!.instalments[0]!.amount_kobo, next_number: 1,
            instalments: sent!.instalments.map((i, n) => ({ number: n + 1, due_date: i.due_date, amount_kobo: i.amount_kobo, paid_kobo: 0, status: 'upcoming' })),
          }),
          201,
        );
      },
    });
    renderApp(
      <Routes>
        <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
      </Routes>,
      '/console/invoices/inv1',
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Pay in instalments' }, { timeout: 5000 }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Adds up to ₦155,000.00 of ₦155,000.00 owed.')).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText('Instalments'), '2');
    expect(within(dialog).getByLabelText('Instalment 2 amount')).toHaveValue('77500');
    // Break the total: the button is disabled until it adds up again
    await userEvent.clear(within(dialog).getByLabelText('Instalment 2 amount'));
    await userEvent.type(within(dialog).getByLabelText('Instalment 2 amount'), '70000');
    expect(within(dialog).getByRole('button', { name: 'Agree plan' })).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText('Instalment 2 amount'));
    await userEvent.type(within(dialog).getByLabelText('Instalment 2 amount'), '77500');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Agree plan' }));

    expect(await screen.findByText(/instalment\s+1 of 2/)).toBeInTheDocument();
    expect(sent!.instalments.map((i) => i.amount_kobo)).toEqual([7_750_000, 7_750_000]);
  });
});
