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
const book = {
  start: today,
  end: today,
  opening_kobo: 1_000_000,
  days: [
    {
      day: today,
      received_kobo: 5_000_000,
      payments: 3,
      reversed_kobo: 0,
      banked_kobo: 4_000_000,
      spent_kobo: 0,
      held_kobo: 2_000_000,
      counted_kobo: 1_800_000,
      difference_kobo: -200_000,
    },
  ],
  staff: [{ user_id: 'u2', name: 'Musa Bello', received_kobo: 5_000_000, payments: 3, banked_kobo: 4_000_000, spent_kobo: 0 }],
  late: [{ kind: 'cash', day: today, amount_kobo: 500_000, recorded_at: `${today}T18:00:00Z`, recorded_by: 'Musa Bello' }],
  held_now_kobo: 2_000_000,
  unchecked_banking_kobo: 4_000_000,
  last_closed: today,
};
const deposit = {
  id: 'd1',
  amount_kobo: 4_000_000,
  banked_on: today,
  bank_reference: 'TLR-778',
  note: '',
  status: 'pending',
  recorded_by: 'u2',
  decided_by: null,
  rejection_reason: '',
  created_at: `${today}T15:00:00Z`,
};

function renderCashBook(capabilities: string[]) {
  const calls = mockApi({
    'POST /api/v1/auth/refresh': () => json(signedIn(me({ capabilities }))),
    'GET /api/v1/cash-book': () => json(book),
    'GET /api/v1/cash-deposits': () => json([deposit]),
    'POST /api/v1/cash-deposits/d1/confirm': () => json({ ...deposit, status: 'confirmed' }),
    'POST /api/v1/cash-deposits': async (request) => json({ ...deposit, ...(await request.json()), id: 'd2' }, 201),
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
    '/console/cash-book',
  );
  return calls;
}

describe('cash book', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('shows what should be held, a short count, late entries and banking to check', async () => {
    const calls = renderCashBook(['payments.view', 'payments.record', 'payments.approve']);
    expect(await screen.findByText('Should be in the drawer now')).toBeInTheDocument();
    const day = within(screen.getByRole('table', { name: 'Cash by day' }));
    expect(day.getByText(/Short/)).toHaveTextContent('Short ₦2,000.00');
    expect(within(screen.getByRole('region', { name: 'Recorded after the day was closed' })).getByText(/Musa Bello/)).toBeInTheDocument();
    const toCheck = within(screen.getByRole('region', { name: 'Banking to check' }));
    expect(toCheck.getByText(/teller TLR-778/)).toBeInTheDocument();
    await userEvent.click(toCheck.getByRole('button', { name: "It's on the statement" }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/cash-deposits/d1/confirm'))).toBe(true));
  });

  it('records banking with its teller number', async () => {
    const calls = renderCashBook(['payments.view', 'payments.record']);
    await userEvent.click(await screen.findByRole('button', { name: 'Record banking' }));
    const dialog = within(screen.getByRole('dialog'));
    await userEvent.type(dialog.getByLabelText('Amount banked (₦)'), '40000');
    await userEvent.type(dialog.getByLabelText('Teller or deposit slip number'), 'TLR-900');
    await userEvent.click(dialog.getByRole('button', { name: 'Record banking' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/cash-deposits'))).toBe(true));
    const sent = await calls.find((c) => c.method === 'POST' && c.url.endsWith('/cash-deposits'))!.json();
    expect(sent).toEqual({ amount_kobo: 4_000_000, banked_on: today, bank_reference: 'TLR-900', note: '' });
    // Without approval rights there's no close or checking
    expect(screen.queryByRole('button', { name: 'Close a day' })).not.toBeInTheDocument();
  });
});
