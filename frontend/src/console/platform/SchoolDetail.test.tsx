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
const staff = (id: string, name: string, overrides: Record<string, unknown> = {}) => ({
  id,
  full_name: name,
  email: `${id}@alpha.ng`,
  role: 'proprietor',
  is_active: true,
  mfa_enabled: true,
  invited: false,
  last_login_at: null,
  ...overrides,
});
const detail = {
  school: {
    id: 's1',
    name: 'Alpha College',
    slug: 'alpha',
    status: 'active',
    address: '',
    phone: '',
    email: '',
    created_at: '2026-10-01T09:00:00Z',
    origin: 'platform',
    plan_name: 'Standard',
    pupils: 3,
    proprietor_email: 'u1@alpha.ng',
    payments_connected: true,
    subscription_owed_kobo: 0,
    last_sign_in_at: null,
  },
  plan_id: null,
  staff: [staff('u1', 'Grace Adeyemi'), staff('u2', 'Old Bursar', { role: 'bursar', is_active: false, mfa_enabled: false })],
  students_active: 3,
  invoices_this_term: 3,
  collected_this_term_kobo: 0,
  collected_30_days_kobo: 0,
  last_activity_at: null,
};

describe('helping a school’s staff', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('sends a password link and resets two-factor after a confirmation', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
      'GET /api/v1/platform/schools/s1': () => json(detail),
      'GET /api/v1/platform/plans': () => json([]),
      'GET /api/v1/platform/billing/invoices': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'POST /api/v1/platform/schools/s1/staff/u1/password-link': () => json({ sent: 'reset' }),
      'POST /api/v1/platform/schools/s1/staff/u1/reset-mfa': () => new Response(null, { status: 204 }),
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
      '/console/platform/schools/s1',
    );
    const staffCard = within(await screen.findByRole('region', { name: 'Staff' }));
    // Deactivated staff get no buttons
    expect(staffCard.getAllByRole('button', { name: 'Send password link' })).toHaveLength(1);
    await userEvent.click(staffCard.getByRole('button', { name: 'Send password link' }));
    expect(await staffCard.findByText('Password-reset link sent to their email.')).toBeInTheDocument();

    await userEvent.click(staffCard.getByRole('button', { name: 'Reset two-factor' }));
    expect(calls.some((c) => c.url.endsWith('/reset-mfa'))).toBe(false); // asks first
    await userEvent.click(staffCard.getByRole('button', { name: 'Yes, reset' }));
    expect(await staffCard.findByText(/Two-factor sign-in turned off/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/staff/u1/reset-mfa'))).toBe(true);
  });
});
