import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '../api/client';
import { error, json, mockApi } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { ParentApp } from './ParentApp';

const child = (overrides: Record<string, unknown>) => ({
  id: 'c1',
  full_name: 'Tolu Adeyemi',
  first_name: 'Tolu',
  admission_number: 'DMC/1',
  class_name: 'JSS 1 A',
  owed_kobo: 15_500_000,
  paid_kobo: 0,
  credit_kobo: 0,
  pay_in: {
    account_number: '9820000015',
    account_name: 'DMC/ADEYEMI TOLU',
    bank_name: 'Wema Bank',
  },
  ...overrides,
});
const home = {
  guardian_name: 'Mrs Adeyemi',
  school_name: 'Demo Model College',
  children: [
    child({}),
    child({
      id: 'c2',
      full_name: 'Bisi Adeyemi',
      first_name: 'Bisi',
      owed_kobo: 0,
      pay_in: null,
    }),
  ],
};
const signedIn = {
  status: 'signed_in',
  access_token: 'parent-token',
  expires_in: 900,
};

function renderAt(path: string) {
  return renderApp(
    <Routes>
      <Route path="/parent/*" element={<ParentApp />} />
    </Routes>,
    path,
  );
}

describe('parent portal', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('signs in with phone and texted code, then shows the children', async () => {
    const calls = mockApi({
      'POST /api/v1/parent/auth/refresh': () => error(401, 'session_expired'),
      'POST /api/v1/parent/auth/request-code': () => json({ detail: 'sent' }, 202),
      'POST /api/v1/parent/auth/verify': () => json(signedIn),
      'GET /api/v1/parent/home': () => json(home),
      'GET /api/v1/parent/preferences': () => json({ reminders: true }),
    });
    renderAt('/parent');
    await userEvent.type(await screen.findByLabelText('Phone number'), '0803 111 2222');
    await userEvent.click(screen.getByRole('button', { name: 'Text me a code' }));
    await userEvent.type(await screen.findByLabelText('Code'), '123456');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Hello, Mrs Adeyemi')).toBeInTheDocument();
    expect(screen.getByText('9820000015')).toBeInTheDocument();
    expect(screen.getByText('All paid. Thank you!')).toBeInTheDocument();
    expect(screen.getByText(/Total owed for 2 children/)).toBeInTheDocument();
    const verify = calls.find((c) => c.url.endsWith('/parent/auth/verify'))!;
    expect(await verify.json()).toEqual({
      phone: '0803 111 2222',
      code: '123456',
    });
    const homeCall = calls.find((c) => c.url.endsWith('/parent/home'))!;
    expect(homeCall.headers.get('Authorization')).toBe('Bearer parent-token');
  });

  it('lets a family at two schools choose one', async () => {
    const calls = mockApi({
      'POST /api/v1/parent/auth/refresh': () => error(401, 'session_expired'),
      'POST /api/v1/parent/auth/request-code': () => json({ detail: 'sent' }, 202),
      'POST /api/v1/parent/auth/verify': () =>
        json({
          status: 'choose_school',
          ticket: 't',
          schools: [
            { guardian_id: 'g1', school_name: 'Alpha', children: ['Tolu'] },
            { guardian_id: 'g2', school_name: 'Beta', children: ['Kemi'] },
          ],
        }),
      'POST /api/v1/parent/auth/choose-school': () => json(signedIn),
      'GET /api/v1/parent/home': () => json(home),
      'GET /api/v1/parent/preferences': () => json({ reminders: true }),
    });
    renderAt('/parent/login');
    await userEvent.type(await screen.findByLabelText('Phone number'), '08031112222');
    await userEvent.click(screen.getByRole('button', { name: 'Text me a code' }));
    await userEvent.type(await screen.findByLabelText('Code'), '654321');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await userEvent.click(await screen.findByRole('button', { name: /Beta/ }));
    expect(await screen.findByText('Hello, Mrs Adeyemi')).toBeInTheDocument();
    const choose = calls.find((c) => c.url.endsWith('/choose-school'))!;
    expect(await choose.json()).toEqual({ ticket: 't', guardian_id: 'g2' });
  });

  it('lets the parent turn off bill messages and reminders', async () => {
    const calls = mockApi({
      'POST /api/v1/parent/auth/refresh': () => json(signedIn),
      'GET /api/v1/parent/home': () => json(home),
      'GET /api/v1/parent/preferences': () => json({ reminders: true }),
      'PUT /api/v1/parent/preferences': async (request) => json(await request.json()),
    });
    renderAt('/parent');
    const toggle = await screen.findByLabelText('Send me new bills and payment reminders');
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    await vi.waitFor(() => expect(toggle).not.toBeChecked());
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(await put.json()).toEqual({ reminders: false });
    expect(screen.getByText("You'll always get a receipt when a payment is received.")).toBeInTheDocument();
  });

  it('lets the parent ask for phone calls in Pidgin when the school makes calls', async () => {
    let prefs = { reminders: true, prefers_calls: false, language: 'en', calls_available: true };
    const calls = mockApi({
      'POST /api/v1/parent/auth/refresh': () => json(signedIn),
      'GET /api/v1/parent/home': () => json(home),
      'GET /api/v1/parent/preferences': () => json(prefs),
      'PUT /api/v1/parent/preferences': async (request) => {
        prefs = { ...prefs, ...(await request.json()) };
        return json(prefs);
      },
    });
    renderAt('/parent');
    await userEvent.click(await screen.findByLabelText(/Also phone me/));
    const language = await screen.findByLabelText('Language on the call');
    await userEvent.selectOptions(language, 'pcm');
    await vi.waitFor(() => expect(screen.getByLabelText('Language on the call')).toHaveValue('pcm'));
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(await puts[0]!.json()).toEqual({ reminders: true, prefers_calls: true });
    expect(await puts[1]!.json()).toEqual({ reminders: true, language: 'pcm' });
  });

  it('restores a session from the parent refresh cookie', async () => {
    const calls = mockApi({
      'POST /api/v1/parent/auth/refresh': () => json(signedIn),
      'GET /api/v1/parent/home': () => json(home),
      'GET /api/v1/parent/preferences': () => json({ reminders: true }),
    });
    renderAt('/parent');
    expect(await screen.findByText('Hello, Mrs Adeyemi')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/api/v1/auth/refresh'))).toBe(false); // not the staff endpoint
  });
});
