import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../api/client';
import { AuthProvider } from '../auth/AuthContext';
import { error, json, me, mockApi, signedIn } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { ConsoleApp } from './ConsoleApp';

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

const noSession = { 'POST /api/v1/auth/refresh': () => error(401, 'session_expired') };
const staffPage = { items: [], total: 0, limit: 50, offset: 0 };

describe('console sign-in', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('sends signed-out visitors to the login page', async () => {
    mockApi(noSession);
    renderConsole('/console/staff');
    expect(await screen.findByRole('heading', { name: 'Sign in to WardPay' })).toBeInTheDocument();
  });

  it('signs in with a password and lands where the user was going', async () => {
    mockApi({
      ...noSession,
      'POST /api/v1/auth/login': () => json(signedIn()),
      'GET /api/v1/staff': () => json(staffPage),
    });
    renderConsole('/console/staff');
    await userEvent.type(await screen.findByLabelText('Email'), 'ada@school.ng');
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('heading', { name: 'Staff' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Invite staff' })).not.toBeInTheDocument();
  });

  it('asks for a two-factor code when the account has MFA', async () => {
    const calls = mockApi({
      ...noSession,
      'POST /api/v1/auth/login': () => json({ status: 'mfa_required', mfa_ticket: 'ticket-1' }),
      'POST /api/v1/auth/login/mfa': () => json(signedIn(me({ mfa_enabled: true }))),
    });
    renderConsole('/console/login');
    await userEvent.type(await screen.findByLabelText('Email'), 'ada@school.ng');
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await userEvent.type(await screen.findByLabelText('Code'), '123456');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { name: /Welcome, Ada/ })).toBeInTheDocument();
    const mfaCall = calls.find((c) => c.url.endsWith('/auth/login/mfa'))!;
    expect(await mfaCall.json()).toEqual({ mfa_ticket: 'ticket-1', totp_code: '123456' });
  });

  it('shows the error for a wrong password', async () => {
    mockApi({ ...noSession, 'POST /api/v1/auth/login': () => error(401, 'invalid_credentials', 'Incorrect email or password.') });
    renderConsole('/console/login');
    await userEvent.type(await screen.findByLabelText('Email'), 'ada@school.ng');
    await userEvent.type(screen.getByLabelText('Password'), 'nope-nope-nope');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.');
  });

  it('keeps users who must enrol in MFA on the account page', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(me({ role: 'bursar', mfa_enrolment_required: true }))),
    });
    renderConsole('/console/staff');
    expect(await screen.findByText(/two-factor sign-in is required/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Staff' })).not.toBeInTheDocument();
  });

  it('restores the session from the refresh cookie and refreshes again after a 401', async () => {
    let staffCalls = 0;
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn()),
      'GET /api/v1/staff': () => (++staffCalls === 1 ? error(401, 'not_authenticated') : json(staffPage)),
    });
    renderConsole('/console/staff');
    expect(await screen.findByText('No staff yet.')).toBeInTheDocument();
    await waitFor(() => expect(staffCalls).toBe(2));
  });
});
