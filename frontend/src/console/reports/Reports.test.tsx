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
const exportJob = (id: string, status: string, filename: string) => ({
  id,
  kind: filename.startsWith('journal') ? 'journal' : 'session_pack',
  status,
  filename: status === 'ready' ? filename : '',
  size_bytes: 3,
  error: '',
  created_at: '2026-10-24T10:00:00Z',
  finished_at: null,
});
const sessions = [
  {
    id: 's1',
    name: '2026/2027',
    start_date: '2026-09-01',
    end_date: '2027-07-31',
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
    expect(screen.getByRole('img', { name: 'Money received on 2 days' })).toBeInTheDocument(); // the first tab
    await userEvent.click(screen.getByRole('tab', { name: 'By class' }));
    const byClass = screen.getByRole('region', { name: 'By class' });
    expect(within(byClass).getByText('2 of 2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'How late' }));
    expect(screen.getByText('Over 90 days late')).toBeInTheDocument();
  });

  it('compares the collection curve with last term and forecasts what falls due', async () => {
    const curve = (label: string, billed: number, collected: number[]) => ({
      term_id: label,
      label,
      start: '2026-09-08',
      billed_kobo: billed,
      days: collected.length - 1,
      collected_kobo: collected,
    });
    mockApi({
      ...session,
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/reports/overview': () => json(overview),
      'GET /api/v1/reports/insights': () =>
        json({
          today: '2026-09-10',
          current: curve('2026/2027 First term', 1000, [100, 300, 540]),
          previous: curve('2025/2026 Third term', 2000, [400, 800, 1220, 1500, 1800]),
          overdue_kobo: 5_000_000,
          windows: [30, 60, 90].map((days) => ({
            days,
            due_kobo: days * 100_000,
            pace_in_kobo: days * 50_000,
            pace_out_kobo: days * 10_000,
          })),
        }),
    });
    renderConsole('/console');
    await userEvent.click(await screen.findByRole('tab', { name: 'Pace and forecast' }));
    const curveSection = within(await screen.findByRole('region', { name: 'Collection curve' }));
    expect(curveSection.getByText('54%')).toBeInTheDocument(); // day 2: 540 of 1,000
    expect(curveSection.getByText('61%')).toBeInTheDocument(); // last term by day 2: 1,220 of 2,000
    expect(curveSection.getByRole('img', { name: 'Share of bills collected by day of term' })).toBeInTheDocument();
    const forecast = within(screen.getByRole('region', { name: 'Cash forecast' }));
    expect(forecast.getByText(/₦50,000.00 is overdue now/)).toBeInTheDocument();
    expect(forecast.getByRole('row', { name: /60 days/ })).toHaveTextContent('₦60,000.00₦30,000.00₦6,000.00');
  });

  it("gives staff who don't see money the school at a glance instead", async () => {
    const admin = me({ role: 'school_admin', capabilities: ['school.view', 'students.view'] });
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
      'GET /api/v1/school/onboarding': () => json([]),
      'GET /api/v1/school/roll': () =>
        json({
          term_id: 't1',
          term_label: '2026/2027 First term',
          on_roll: 240,
          placed: 236,
          unplaced: 4,
          joined_this_term: 12,
          left_this_term: 3,
          no_parent: 2,
          no_consent: 0,
          no_account_number: 1,
          parents: 180,
          by_class: [
            {
              class_level_id: 'l1',
              class_name: 'JSS 1',
              pupils: 40,
              boys: 21,
              girls: 19,
              arms: [
                { arm_id: 'a1', arm_name: 'A', pupils: 20 },
                { arm_id: 'a2', arm_name: 'B', pupils: 20 },
              ],
            },
          ],
        }),
    });
    renderConsole('/console');
    expect(await screen.findByText('240')).toBeInTheDocument();
    expect(screen.getByText('12 joined this term · 3 left')).toBeInTheDocument();
    expect(screen.getByText('98% of pupils')).toBeInTheDocument();
    const attention = screen.getByRole('region', { name: 'Needs attention' });
    expect(within(attention).getByRole('link', { name: '4 pupils' })).toHaveAttribute('href', '/console/students?show=unplaced');
    expect(within(attention).getByRole('link', { name: '2 pupils' })).toHaveAttribute('href', '/console/students?show=parent');
    expect(within(attention).getByRole('link', { name: '1 pupil' })).toHaveAttribute('href', '/console/students?show=account_number');
    expect(within(attention).queryByText(/consent/)).not.toBeInTheDocument(); // nothing to fix there
    const byClass = screen.getByRole('region', { name: 'Pupils by class' });
    expect(within(byClass).getByRole('link', { name: 'JSS 1' })).toHaveAttribute('href', '/console/students?class=l1');
    expect(within(byClass).getByText('A: 20 · B: 20')).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/reports/'))).toBe(false); // never asks for money figures
    expect(screen.queryByRole('link', { name: 'Reports' })).not.toBeInTheDocument();
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

  it('downloads every pupil’s fee status for one class', async () => {
    const calls = mockApi({
      ...session,
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/class-levels': () => json([{ id: 'l3', name: 'JSS 3', rank: 3, is_active: true, arms: [] }]),
      'GET /api/v1/reports/debtors': () => json({ items: [], total: 0, limit: 50, offset: 0, total_owed_kobo: 0 }),
      'GET /api/v1/reports/fee-status/export': () =>
        new Response('pdf', { headers: { 'Content-Disposition': 'attachment; filename="fee-status-jss-3-2026-10-20.pdf"' } }),
    });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();

    renderConsole('/console/reports');
    await userEvent.click(await screen.findByRole('tab', { name: 'Fee status by pupil' }));
    const panel = screen.getByRole('region', { name: 'Fee status by pupil' });
    await userEvent.selectOptions(within(panel).getByLabelText('Class'), 'l3');
    await userEvent.click(within(panel).getByRole('button', { name: 'Download PDF' }));
    await vi.waitFor(() => expect(saved).toEqual(['fee-status-jss-3-2026-10-20.pdf']));
    const exported = new URL(calls.find((c) => c.url.includes('/fee-status/export'))!.url);
    expect(exported.searchParams.get('format')).toBe('pdf');
    expect(exported.searchParams.get('class_level_id')).toBe('l3');
    expect(exported.searchParams.get('term_id')).toBeNull(); // the current term
  });

  it('gives the accountant the journal and lets the bursar use their own account codes', async () => {
    const bank = { key: 'bank', code: '1010', name: 'Bank', type: 'Asset', default_code: '1010', default_name: 'Bank' };
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(me({ role: 'bursar', capabilities: [...bursar.capabilities, 'payments.approve'] }))),
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/reports/debtors': () => json({ items: [], total: 0, limit: 50, offset: 0, total_owed_kobo: 0 }),
      'GET /api/v1/accounting/journal': () =>
        json({
          start: '2026-10-01',
          end: '2026-10-20',
          entries: 12,
          debit_kobo: 5_000_000,
          credit_kobo: 5_000_000,
          totals: [{ account: bank, debit_kobo: 5_000_000, credit_kobo: 0 }],
          unchecked_deposits: 1,
        }),
      'GET /api/v1/accounting/accounts': () => json([bank]),
      'PUT /api/v1/accounting/accounts/bank': async (request) => json({ ...bank, ...(await request.json()), name: 'GTBank current' }),
      // The journal is built in the background: pending first, ready when checked; the pack is ready at once
      'POST /api/v1/accounting/exports': async (request) => {
        const body = (await request.json()) as { kind: string };
        return json(
          body.kind === 'journal'
            ? exportJob('x1', 'pending', 'journal-2026-10-01-to-2026-10-20.csv')
            : exportJob('x2', 'ready', 'end-of-session-2026-2027.xlsx'),
          202,
        );
      },
      'GET /api/v1/accounting/exports/x1': () => json(exportJob('x1', 'ready', 'journal-2026-10-01-to-2026-10-20.csv')),
      'GET /api/v1/accounting/exports/x1/download': () =>
        new Response('csv', { headers: { 'Content-Disposition': 'attachment; filename="journal-2026-10-01-to-2026-10-20.csv"' } }),
      'GET /api/v1/accounting/exports/x2/download': () =>
        new Response('xlsx', { headers: { 'Content-Disposition': 'attachment; filename="end-of-session-2026-2027.xlsx"' } }),
    });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();

    renderConsole('/console/reports');
    await userEvent.click(await screen.findByRole('tab', { name: 'For your accountant' }));
    const totals = within(await screen.findByRole('region', { name: 'Account totals' }));
    expect(totals.getByText('₦50,000.00')).toBeInTheDocument();
    expect(screen.getByText(/1 cash deposit is left out/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await vi.waitFor(() => expect(saved).toEqual(['journal-2026-10-01-to-2026-10-20.csv']), { timeout: 4000 });

    await userEvent.click(screen.getByRole('button', { name: 'Download the pack' }));
    await vi.waitFor(() => expect(saved).toContain('end-of-session-2026-2027.xlsx'));
    const asked = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/accounting/exports'));
    expect(await asked[1]!.json()).toEqual({ kind: 'session_pack', session_id: sessions[0]!.id });

    const name = await screen.findByLabelText('Name for Bank');
    await userEvent.clear(name);
    await userEvent.type(name, 'GTBank current');
    await userEvent.click(within(screen.getByRole('region', { name: 'Chart of accounts' })).getByRole('button', { name: 'Save' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(await calls.find((c) => c.method === 'PUT')!.json()).toEqual({ code: '', name: 'GTBank current' }); // code left at the default
  });

  it('closes an ended term and lets the proprietor re-open it with a reason', async () => {
    const ended = [
      {
        id: 's0',
        name: '2025/2026',
        start_date: '2025-09-01',
        end_date: '2026-07-31',
        terms: [{ id: 't3', label: 'Third term', is_current: false, start_date: '2026-04-20', end_date: '2026-07-24' }],
      },
    ];
    let closedThrough: string | null = null;
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () =>
        json(signedIn(me({ role: 'proprietor', capabilities: [...bursar.capabilities, 'payments.approve', 'books.reopen'] }))),
      'GET /api/v1/academic-sessions': () => json(ended),
      'GET /api/v1/reports/debtors': () => json({ items: [], total: 0, limit: 50, offset: 0, total_owed_kobo: 0 }),
      'GET /api/v1/accounting/journal': () =>
        json({ start: '2026-10-01', end: '2026-10-20', entries: 0, debit_kobo: 0, credit_kobo: 0, totals: [], unchecked_deposits: 0 }),
      'GET /api/v1/accounting/accounts': () => json([]),
      'GET /api/v1/accounting/books': () => json({ closed_through: closedThrough, history: [] }),
      'POST /api/v1/accounting/terms/t3/close': () => {
        closedThrough = '2026-07-24';
        return json({
          id: 'b1',
          action: 'close',
          term_id: 't3',
          closed_through_before: null,
          closed_through_after: '2026-07-24',
          reason: '',
          figures: {},
          by: 'u1',
          created_at: '2026-10-24T10:00:00Z',
        });
      },
      'POST /api/v1/accounting/books/reopen': () =>
        json({
          id: 'b2',
          action: 'reopen',
          term_id: 't3',
          closed_through_before: '2026-07-24',
          closed_through_after: null,
          reason: 'Late teller',
          figures: {},
          by: 'u1',
          created_at: '2026-10-24T11:00:00Z',
        }),
    });
    renderConsole('/console/reports');
    await userEvent.click(await screen.findByRole('tab', { name: 'For your accountant' }));
    const books = within(await screen.findByRole('region', { name: 'Closing the books' }));
    expect(books.getByText('No term has been closed yet.')).toBeInTheDocument();
    await userEvent.click(books.getByRole('button', { name: 'Close 2025/2026 Third term' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close the term' }));
    expect(await books.findByText(/The books are closed up to/)).toBeInTheDocument();

    await userEvent.click(books.getByRole('button', { name: 'Re-open the last closed term' }));
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByRole('button', { name: 'Re-open' })).toBeDisabled(); // a reason first
    await userEvent.type(dialog.getByLabelText('Why'), 'Late teller');
    await userEvent.click(dialog.getByRole('button', { name: 'Re-open' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/books/reopen'))).toBe(true));
    expect(await calls.find((c) => c.url.endsWith('/books/reopen'))!.json()).toEqual({ reason: 'Late teller' });
  });

  it('shows the proprietor unusual activity, flagged on the dashboard too', async () => {
    const proprietor = me({ role: 'proprietor', capabilities: [...bursar.capabilities, 'staff.manage'] });
    const unusual = {
      start: '2026-09-25',
      end: '2026-10-24',
      count: 2,
      signals: [
        {
          key: 'reversals',
          title: 'Payments reversed',
          why: 'A reversal undoes a receipt. Musa Bello reversed 3 or more.',
          serious: true,
          items: [{ day: '2026-10-22', what: 'Teller bounced', amount_kobo: -100_000, who: 'Musa Bello', link: '/console/students/s1' }],
        },
        {
          key: 'duplicate_spending',
          title: 'Spending that looks entered twice',
          why: 'The same payee and amount within 7 days.',
          serious: false,
          items: [
            {
              day: '2026-10-20',
              what: 'Total filling station, also on 18 Oct',
              amount_kobo: 750_000,
              who: 'Ada Obi',
              link: '/console/expenses',
            },
          ],
        },
      ],
    };
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(proprietor)),
      'GET /api/v1/academic-sessions': () => json(sessions),
      'GET /api/v1/reports/overview': () => json(overview),
      'GET /api/v1/reports/unusual-activity': () => json(unusual),
      'GET /api/v1/reports/debtors': () => json({ items: [], total: 0, limit: 50, offset: 0, total_owed_kobo: 0 }),
    });
    renderConsole('/console');
    expect(await screen.findByText(/Worth a look: payments reversed/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'See unusual activity →' }));
    const reversals = within(await screen.findByRole('region', { name: 'Payments reversed' }));
    expect(reversals.getByText('Look at this')).toBeInTheDocument();
    expect(reversals.getByRole('link', { name: 'Teller bounced' })).toHaveAttribute('href', '/console/students/s1');
    expect(reversals.getByText('Musa Bello')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Spending that looks entered twice' })).getByText('Ada Obi')).toBeInTheDocument();
  });

  it('hides unusual activity from staff who are not the proprietor', async () => {
    mockApi({
      ...session,
      'GET /api/v1/reports/debtors': () => json({ items: [], total: 0, limit: 50, offset: 0, total_owed_kobo: 0 }),
      'GET /api/v1/academic-sessions': () => json(sessions),
    });
    renderConsole('/console/reports');
    expect(await screen.findByRole('tab', { name: 'For your accountant' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Unusual activity' })).not.toBeInTheDocument();
  });
});
