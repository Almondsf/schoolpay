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
const REFERENCE = 'WS' + 'B2'.repeat(10);
const problems = {
  webhooks: [
    {
      id: 'w1',
      gateway: 'paystack',
      status: 'needs_review',
      event_type: 'charge.success',
      reference: 'T123',
      school_name: null,
      claimed_amount_kobo: 500_000,
      note: 'Unknown receiving account',
      attempts: 1,
      received_at: '2026-10-22T09:00:00Z',
    },
  ],
  checkouts: [
    {
      kind: 'subscription',
      reference: REFERENCE,
      school_name: 'Alpha College',
      amount_kobo: 10_000_000,
      review_reason: 'Paid 100 kobo, expected 10000000',
      created_at: '2026-10-22T10:00:00Z',
    },
  ],
  tasks: [{ id: 't1', task_name: 'email.send', attempts: 8, last_error: 'SMTP down', created_at: '2026-10-21T10:00:00Z' }],
  messages: [{ school_name: 'Alpha College', failed: 12, last_error: 'Termii refused the message (HTTP 402)' }],
};

describe('problems to fix', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('lists what is stuck, retries a notification and resolves a payment with a note', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(platformAdmin)),
      'GET /api/v1/platform/problems': () => json(problems),
      'POST /api/v1/platform/problems/webhooks/w1/retry': () => new Response(null, { status: 204 }),
      [`POST /api/v1/platform/problems/checkouts/${REFERENCE}/resolve`]: () => new Response(null, { status: 204 }),
      'POST /api/v1/platform/problems/tasks/t1/retry': () => new Response(null, { status: 204 }),
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
      '/console/platform/problems',
    );
    const notifications = within(await screen.findByRole('region', { name: 'Payment notifications' }));
    expect(notifications.getByText('Unknown receiving account')).toBeInTheDocument();
    await userEvent.click(notifications.getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/problems/webhooks/w1/retry'))).toBe(true));

    const review = within(screen.getByRole('region', { name: 'Card payments in review' }));
    expect(review.getByText('Subscription bill')).toBeInTheDocument();
    await userEvent.click(review.getByRole('button', { name: 'Resolve' }));
    await userEvent.type(screen.getByLabelText('What was done'), 'Refunded by Paystack');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Resolve' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith(`/checkouts/${REFERENCE}/resolve`))).toBe(true));
    const resolved = calls.find((c) => c.url.endsWith(`/checkouts/${REFERENCE}/resolve`))!;
    expect(await resolved.json()).toEqual({ note: 'Refunded by Paystack' });

    await userEvent.click(within(screen.getByRole('region', { name: 'Background tasks' })).getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/problems/tasks/t1/retry'))).toBe(true));
    expect(within(screen.getByRole('region', { name: 'Failed messages' })).getByText('12')).toBeInTheDocument();
  });
});
