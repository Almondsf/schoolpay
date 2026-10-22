import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { json, mockApi } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { PlatformTrends, monthLabel } from './Trends';

const month = (m: string, overrides: Record<string, number> = {}) => ({
  month: m,
  processed_kobo: 0,
  payments: 0,
  subscription_received_kobo: 0,
  schools_joined: 0,
  schools_suspended: 0,
  ...overrides,
});

describe('platform trends', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('totals the year for each measure, one bar per month', async () => {
    mockApi({
      'GET /api/v1/platform/trends': () =>
        json([
          month('2026-09', { subscription_received_kobo: 10_000_000, processed_kobo: 50_000_000, schools_joined: 2 }),
          month('2026-10', { subscription_received_kobo: 5_000_000, processed_kobo: 25_000_000, schools_joined: 1, schools_suspended: 1 }),
        ]),
    });
    renderApp(<PlatformTrends />);
    expect(await screen.findByText('₦150,000.00')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /received from schools per month/ }).querySelectorAll('rect')).toHaveLength(2);
    await userEvent.click(screen.getByRole('tab', { name: /Money through/ }));
    expect(screen.getByText('₦750,000.00')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Schools' }));
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.getByText(/3 joined, 1 suspended/)).toBeInTheDocument();
  });

  it('labels months briefly', () => {
    expect(monthLabel('2026-10')).toBe('Oct 26');
  });
});
