import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const admin = me({ capabilities: ['school.view', 'school.manage'] });
const suggested = {
  name: '2026/2027',
  start_date: '2026-09-01',
  end_date: '2027-07-31',
  terms: [
    { start_date: '2026-09-14', end_date: '2026-12-11' },
    { start_date: '2027-01-11', end_date: '2027-04-02' },
    { start_date: '2027-04-26', end_date: '2027-07-16' },
  ],
  current_term: 2,
};

describe('setting up the school year', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('fills in the usual dates and makes the running term current', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
      'GET /api/v1/academic-sessions': () => json([]),
      'GET /api/v1/academic-sessions/suggested': () => json(suggested),
      'POST /api/v1/academic-sessions': () => json({}, 201),
    });
    renderApp(
      <Routes>
        <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
      </Routes>,
      '/console/calendar',
    );
    expect(await screen.findByRole('heading', { name: 'Set up your school year' })).toBeInTheDocument();
    expect(await screen.findByDisplayValue('2026/2027')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Make the second term the current term/ })).toBeChecked();
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument(); // the form is already here

    await userEvent.click(screen.getByRole('button', { name: 'Create 2026/2027' }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/academic-sessions'))).toBe(true));
    const sent = calls.find((c) => c.method === 'POST' && c.url.endsWith('/academic-sessions'))!;
    const { current_term: _ignored, ...dates } = suggested;
    expect(await sent.json()).toEqual({ ...dates, current_term: 2 });
  });
});
