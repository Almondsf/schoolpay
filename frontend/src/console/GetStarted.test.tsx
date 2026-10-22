import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../api/client';
import { AuthProvider } from '../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { ConsoleApp } from './ConsoleApp';

const admin = me({ capabilities: ['school.view', 'school.manage', 'fees.view', 'fees.manage', 'invoices.issue'] });
const steps = (done: string[]) =>
  ['profile', 'calendar', 'classes', 'students', 'fees', 'staff', 'bank', 'account_numbers', 'bills'].map((key) => ({
    key,
    title: key,
    hint: '',
    link: '/console',
    done: done.includes(key),
  }));
const level = (id: string, name: string) => ({ id, name, rank: 1, is_active: true, arms: [] });

describe('set-up wizard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('opens on the first step not done and sets fees once per section for every class in it', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
      'GET /api/v1/school/onboarding': () => json(steps(['profile', 'calendar', 'classes'])),
      'GET /api/v1/terms/current': () => json({ id: 't1', session_id: 's1', number: 1, label: 'First term', session_name: '2026/2027', is_current: true }),
      'GET /api/v1/class-levels': () => json([level('p1', 'Primary 1'), level('p2', 'Primary 2'), level('j1', 'JSS 1'), level('y7', 'Year 7')]),
      'GET /api/v1/class-levels/standard': () =>
        json([
          { group: 'Primary', name: 'Primary 1', exists: true },
          { group: 'Primary', name: 'Primary 2', exists: true },
          { group: 'Junior secondary', name: 'JSS 1', exists: true },
        ]),
      'GET /api/v1/fee-items': () => json([{ id: 'tu', name: 'Tuition', category: 'tuition', is_active: true, default_amount_kobo: null }]),
      'POST /api/v1/fee-items': async (request) => json({ id: 'new', ...(await request.json()), is_active: true, default_amount_kobo: null }, 201),
      'PUT /api/v1/terms/t1/fee-schedule': async (request) => json({ entries: (await request.json()).entries, bills: { dry_run: false, fees: [], bills_changed: 0, total_kobo: 0 } }),
    });
    renderApp(
      <Routes>
        <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
      </Routes>,
      '/console/get-started',
    );
    expect(await screen.findByRole('heading', { name: '4. Fees' })).toBeInTheDocument();
    await userEvent.type(await screen.findByLabelText('Tuition for Primary'), '120000');
    await userEvent.type(screen.getByLabelText('Tuition for Junior secondary'), '150000');
    await userEvent.type(screen.getByLabelText('PTA levy for Other'), '5000'); // Year 7 isn't on the usual list
    await userEvent.click(screen.getByRole('button', { name: 'Save fees and continue' }));

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const created = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/fee-items'));
    expect(await Promise.all(created.map((c) => c.json()))).toEqual([{ name: 'PTA levy', category: 'levy' }]); // Tuition already existed
    const { entries } = await calls.find((c) => c.method === 'PUT')!.json();
    expect(entries).toEqual([
      { fee_item_id: 'tu', class_level_id: 'p1', amount_kobo: 12_000_000, is_optional: false },
      { fee_item_id: 'tu', class_level_id: 'p2', amount_kobo: 12_000_000, is_optional: false },
      { fee_item_id: 'tu', class_level_id: 'j1', amount_kobo: 15_000_000, is_optional: false },
      { fee_item_id: 'new', class_level_id: 'y7', amount_kobo: 500_000, is_optional: false },
    ]);
    expect(await screen.findByRole('heading', { name: '5. Pupils' })).toBeInTheDocument();
  });
});
