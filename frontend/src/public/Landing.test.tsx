import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { json, mockApi } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { Landing } from './Landing';

const info = (signup_open: boolean) => ({
  app_name: 'WardPay',
  support_email: 'help@example.com',
  signup_open,
  setup_fee_kobo: 5_000_000,
  annual_fee_kobo: 10_000_000,
  per_pupil_term_kobo: 50_000,
});

describe('home page', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows how pricing works without figures, the parents band and the questions schools ask', async () => {
    mockApi({ 'GET /api/v1/public/info': () => json(info(true)) });
    renderApp(<Landing />);
    const pricing = await screen.findByRole('region', { name: 'Pricing for schools' });
    expect(within(pricing).getByText('Pupil fee')).toBeInTheDocument();
    expect(within(pricing).queryByText(/₦/)).not.toBeInTheDocument();
    await screen.findByText('© ' + new Date().getFullYear() + ' WardPay', { exact: false });
    expect(screen.getByRole('img', { name: /smiling pupil/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'No queues at the bank. No lost tellers.' })).toBeInTheDocument();
    expect(screen.getByText('Do you hold our money?')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Sign your school up/ }).length).toBeGreaterThan(1);
  });

  it('hides every sign-up link when sign-up is closed, but parents can still pay', async () => {
    mockApi({ 'GET /api/v1/public/info': () => json(info(false)) });
    renderApp(<Landing />);
    await vi.waitFor(() => expect(screen.queryByRole('link', { name: /Sign your school up/ })).not.toBeInTheDocument());
    expect(screen.getAllByRole('link', { name: /pay school fees/i }).length).toBeGreaterThan(0);
  });
});
