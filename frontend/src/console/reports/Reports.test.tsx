import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';
import { percent } from '../Dashboard';

const bursar = me({
  role: 'bursar',
  capabilities: ['school.view', 'fees.view', 'payments.view', 'reports.view'],
});
const session = { 'POST /api/v1/auth/refresh': () => json(signedIn(bursar)) };

const overview = {
  term_id: 't1',
  term_label: '2026/2027 First term',
  term_start: '2026-09-07',
  term_end: '2026-12-11',
  invoices: 3,
  billed_kobo: 54_500_000,
  collected_kobo: 20_500_000,
  outstanding_kobo: 34_000_000,
  total_outstanding_kobo: 38_000_000,
  older_outstanding_kobo: 4_000_000,
  students_owing: 2,
  credit_kobo: 1_000_000,
  received_today_kobo: 0,
  received_7_days_kobo: 6_000_000,
  received_this_term_kobo: 21_500_000,
  pending_approval: 1,
  pending_approval_kobo: 5_000_000,
  by_class: [
    {
      class_level_id: 'l1',
      class_name: 'JSS 1',
      invoices: 1,
      billed_kobo: 15_500_000,
      collected_kobo: 15_500_000,
      outstanding_kobo: 0,
      students_owing: 0,
    },
    {
      class_level_id: 'l2',
      class_name: 'JSS 2',
      invoices: 2,
      billed_kobo: 39_000_000,
      collected_kobo: 5_000_000,
      outstanding_kobo: 34_000_000,
      students_owing: 2,
    },
  ],
  by_day: [
    { day: '2026-09-20', amount_kobo: 15_500_000, payments: 1 },
    { day: '2026-10-01', amount_kobo: 6_000_000, payments: 2 },
  ],
  by_method: [
    { method: 'teller', amount_kobo: 15_500_000, payments: 1 },
    { method: 'cash', amount_kobo: 6_000_000, payments: 2 },
  ],
  aging: [
    { bucket: 'not_due', amount_kobo: 0, invoices: 0 },
    { bucket: '1_30', amount_kobo: 34_000_000, invoices: 2 },
    { bucket: '31_60', amount_kobo: 0, invoices: 0 },
    { bucket: '61_90', amount_kobo: 0, invoices: 0 },
    { bucket: 'over_90', amount_kobo: 4_000_000, invoices: 1 },
  ],
};
const sessions = [
  {
    id: 's1',
    name: '2026/2027',
    terms: [{ id: 't1', label: 'First term', is_current: true }],
  },
];

function renderConsole(path: string) {
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

describe('dashboard and reports', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('never rounds an unfinished collection up to 100%', () => {
    expect(percent(995, 1000)).toBe(99);
    expect(percent(1, 0)).toBe(0);
  });

  it('shows the term at a glance', async () => {
    mockApi({
      ...session,
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/reports/overview': () => json(overview),
    });
    renderConsole('/console');
    expect(await screen.findByText('₦545,000.00')).toBeInTheDocument();
    expect(screen.getByText("37% of the term's bills")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '2 students owe' })).toHaveAttribute('href', '/console/reports');
    expect(screen.getByRole('link', { name: '1 cash or teller entries to check' })).toBeInTheDocument();
    const byClass = screen.getByRole('region', { name: 'By class' });
    expect(within(byClass).getByText('2 of 2')).toBeInTheDocument();
    expect(screen.getByText('Over 90 days late')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Money received on 2 days' })).toBeInTheDocument();
  });

  it('points to billing before the term has bills', async () => {
    mockApi({
      ...session,
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/reports/overview': () => json({ ...overview, invoices: 0, by_class: [] }),
    });
    renderConsole('/console');
    expect(await screen.findByRole('link', { name: "Generate the term's bills" })).toBeInTheDocument();
  });

  it('staff without money access get a plain welcome', async () => {
    mockApi({ 'POST /api/v1/auth/refresh': () => json(signedIn(me())) });
    renderConsole('/console');
    expect(await screen.findByText(/Use the menu to manage students/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Reports' })).not.toBeInTheDocument();
  });

  it('lists debtors with filters and downloads the list', async () => {
    const debtor = {
      student_id: 's1',
      admission_number: 'DMC/1',
      full_name: 'Chidi Okafor',
      status: 'active',
      class_name: 'JSS 3 A',
      owed_kobo: 24_500_000,
      oldest_due: '2026-07-01',
      days_overdue: 111,
      guardian_name: 'Mr Okafor',
      guardian_phone: '+2348030000003',
      reminders_off: true,
      reminders_sent: 0,
      last_paid_at: null,
    };
    const calls = mockApi({
      ...session,
      'GET /api/v1/class-levels': () => json([{ id: 'l3', name: 'JSS 3', rank: 3, is_active: true, arms: [] }]),
      'GET /api/v1/reports/debtors': () =>
        json({
          items: [debtor],
          total: 1,
          limit: 50,
          offset: 0,
          total_owed_kobo: 24_500_000,
        }),
      'GET /api/v1/reports/debtors/export': () =>
        new Response('xlsx', {
          headers: {
            'Content-Disposition': 'attachment; filename="debtors-2026-10-20.xlsx"',
          },
        }),
    });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();

    renderConsole('/console/reports');
    expect(await screen.findByRole('link', { name: 'Chidi Okafor' })).toBeInTheDocument();
    expect(screen.getByText('111 days')).toBeInTheDocument();
    expect(screen.getByText('Turned off')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '+2348030000003' })).toHaveAttribute('href', 'tel:+2348030000003');

    await userEvent.selectOptions(screen.getByLabelText('Class'), 'l3');
    await userEvent.click(screen.getByLabelText('Overdue only'));
    await vi.waitFor(() => {
      const url = new URL(calls[calls.length - 1]!.url);
      expect(url.searchParams.get('class_level_id')).toBe('l3');
      expect(url.searchParams.get('overdue_only')).toBe('true');
    });

    await userEvent.click(screen.getByRole('button', { name: 'Download Excel' }));
    await vi.waitFor(() => expect(saved).toEqual(['debtors-2026-10-20.xlsx']));
    const exported = new URL(calls.find((c) => c.url.includes('/debtors/export'))!.url);
    expect(exported.searchParams.get('format')).toBe('xlsx');
    expect(exported.searchParams.get('class_level_id')).toBe('l3');
  });
});
