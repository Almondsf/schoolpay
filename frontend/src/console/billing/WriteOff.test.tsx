import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const invoice = {
  id: 'inv1',
  number: 12,
  kind: 'term',
  term_id: 't1',
  student: { id: 's1', admission_number: 'DMC/1', full_name: 'Tolu Adeyemi' },
  issued_at: '2026-09-07T08:00:00Z',
  due_date: '2026-09-30',
  total_kobo: 15_500_000,
  paid_kobo: 5_500_000,
  balance_kobo: 10_000_000,
  status: 'part_paid',
  void_reason: null,
  lines: [],
  plan: null,
};
const waiting = {
  id: 'w1',
  invoice_id: 'inv1',
  invoice_number: 12,
  student_id: 's1',
  student_name: 'Tolu Adeyemi',
  admission_number: 'DMC/1',
  amount_kobo: 6_000_000,
  reason: 'Family relocated abroad',
  status: 'pending',
  requested_by: 'u2',
  decided_by: null,
  rejection_reason: '',
  created_at: '2026-10-24T10:00:00Z',
};

function renderAt(path: string) {
  return renderApp(
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
}

describe('writing off debt', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('lets the bursar ask the proprietor to write off part of a bill', async () => {
    const bursar = me({ role: 'bursar', capabilities: ['fees.view', 'invoices.issue', 'students.view', 'payments.approve'] });
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(bursar)),
      'GET /api/v1/invoices/inv1': () => json(invoice),
      'POST /api/v1/invoices/inv1/write-off': async (request) => json({ ...waiting, ...(await request.json()) }, 201),
    });
    renderAt('/console/invoices/inv1');
    await userEvent.click(await screen.findByRole('button', { name: 'Write off' }, { timeout: 5000 }));
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText(/The proprietor approves it before anything changes/)).toBeInTheDocument();
    expect(dialog.getByLabelText(/^Amount/)).toHaveValue('100000'); // what's still owed
    await userEvent.clear(dialog.getByLabelText(/^Amount/));
    await userEvent.type(dialog.getByLabelText(/^Amount/), '60000');
    await userEvent.type(dialog.getByLabelText('Why'), 'Family relocated abroad');
    await userEvent.click(dialog.getByRole('button', { name: 'Ask the proprietor' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/write-off'))).toBe(true));
    expect(await calls.find((c) => c.url.endsWith('/write-off'))!.json()).toEqual({
      amount_kobo: 6_000_000,
      reason: 'Family relocated abroad',
    });
  });

  it('shows the proprietor what is waiting, to approve', async () => {
    const proprietor = me({
      role: 'proprietor',
      capabilities: ['payments.view', 'payments.approve', 'debts.write_off', 'fees.view'],
    });
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(proprietor)),
      'GET /api/v1/payments': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'GET /api/v1/manual-payments': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'GET /api/v1/write-offs': () => json([waiting]),
      'POST /api/v1/write-offs/w1/approve': () => json({ ...waiting, status: 'approved' }),
    });
    renderAt('/console/payments');
    await userEvent.click(await screen.findByRole('tab', { name: /Awaiting approval/ }));
    const section = within(await screen.findByRole('region', { name: 'Write-offs waiting' }));
    expect(section.getByText('Family relocated abroad')).toBeInTheDocument();
    expect(section.getByText('₦60,000.00')).toBeInTheDocument();
    await userEvent.click(section.getByRole('button', { name: 'Approve' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/write-offs/w1/approve'))).toBe(true));
  });
});
