import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FamilyLinkDialog } from '../console/students/FamilyLink';
import { error, json, mockApi } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { FamilyPay } from './FamilyPay';

const TOKEN = 'abcdefghijklmnopqrstuvwxyz012345';

const page = {
  school_name: 'Alpha College',
  card_payments: true,
  min_part_kobo: 100_000,
  children: [
    {
      student_id: 's1',
      first_name: 'Ada',
      class_name: 'JSS 1 A',
      owed_kobo: 15_500_000,
      suggested_kobo: 15_500_000,
      on_plan: false,
      account_number: '9900000001',
      bank_name: 'Wema Bank',
      fees: [
        { name: 'Tuition', billed_kobo: 15_000_000, paid_kobo: 0, left_kobo: 15_000_000, status: 'Unpaid' },
        { name: 'WAEC', billed_kobo: 400_000, paid_kobo: 0, left_kobo: 400_000, status: 'Unpaid' },
        { name: 'PTA', billed_kobo: 300_000, paid_kobo: 200_000, left_kobo: 100_000, status: 'Part paid' },
      ],
      earlier_left_kobo: 0,
    },
    {
      student_id: 's2',
      first_name: 'Bayo',
      class_name: 'JSS 2 A',
      owed_kobo: 18_500_000,
      suggested_kobo: 5_000_000,
      on_plan: true,
      account_number: null,
      bank_name: null,
      fees: [{ name: 'Tuition', billed_kobo: 18_500_000, paid_kobo: 0, left_kobo: 18_500_000, status: 'Unpaid' }],
      earlier_left_kobo: 2_000_000,
    },
    {
      student_id: 's3',
      first_name: 'Chidi',
      class_name: 'SSS 1 Arts',
      owed_kobo: 0,
      suggested_kobo: 0,
      on_plan: false,
      account_number: '9900000003',
      bank_name: 'Wema Bank',
      fees: [{ name: 'Tuition', billed_kobo: 20_000_000, paid_kobo: 20_000_000, left_kobo: 0, status: 'Paid' }],
      earlier_left_kobo: 0,
    },
  ],
};

function renderPage() {
  return renderApp(
    <Routes>
      <Route path="/pay/f/:token" element={<FamilyPay />} />
    </Routes>,
    `/pay/f/${TOKEN}`,
  );
}

describe('family payment page', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('starts with what is due now per child and pays the split the parent chose', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    const calls = mockApi({
      [`GET /api/v1/public/pay/${TOKEN}`]: () => json(page),
      [`POST /api/v1/public/pay/${TOKEN}/checkout`]: () =>
        json({ reference: 'SP1', amount_kobo: 0, authorization_url: 'https://gateway.example/pay/SP1' }, 201),
    });
    renderPage();
    const ada = await screen.findByRole('region', { name: 'Ada' });
    expect(within(ada).getByText('9900000001')).toBeInTheDocument();
    const adaFees = within(ada).getByRole('list', { name: 'Fees' });
    expect(within(adaFees).getByText('WAEC')).toBeInTheDocument();
    expect(within(adaFees).getByText(/₦1,000.00 left/)).toBeInTheDocument(); // PTA part paid
    expect(within(screen.getByRole('region', { name: 'Bayo' })).getByText('From earlier terms')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Bayo' })).getByText(/₦50,000.00 is due next/)).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Chidi' })).getByText('All paid')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Amount for Chidi/)).not.toBeInTheDocument();

    // Starts at Ada's 155k + Bayo's next instalment (50k)
    const top = within(screen.getByRole('region', { name: 'Pay for all your children' }));
    expect(top.getByRole('button', { name: 'Pay ₦205,000.00 now' })).toBeEnabled();
    expect(top.getByText(/bank transfer/)).toBeInTheDocument();
    const bayo = screen.getByLabelText(/Amount for Bayo/);
    await userEvent.clear(bayo);
    await userEvent.type(bayo, '200000');
    expect(screen.getByText('Up to ₦185,000.00')).toBeInTheDocument();
    expect(top.getByRole('button', { name: /now$/ })).toBeDisabled();
    await userEvent.clear(bayo); // Bayo another time
    await userEvent.click(top.getByRole('button', { name: 'Pay ₦155,000.00 now' }));

    const post = calls.find((c) => c.method === 'POST')!;
    expect(await post.json()).toEqual({ items: [{ student_id: 's1', amount_kobo: 15_500_000 }] });
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('https://gateway.example/pay/SP1'));
  });

  it('says plainly when a link no longer works', async () => {
    mockApi({
      [`GET /api/v1/public/pay/${TOKEN}`]: () =>
        error(404, 'not_found', 'This payment link has expired or been replaced. Ask the school to send you a new one.'),
    });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('expired or been replaced');
  });

  it('lets staff copy or replace a parent link', async () => {
    const first = { url: 'https://app.example/pay/f/old', expires_at: '2027-02-01T00:00:00Z' };
    const second = { url: 'https://app.example/pay/f/new', expires_at: '2027-02-20T00:00:00Z' };
    mockApi({
      'GET /api/v1/guardians/g1/pay-link': () => json(first),
      'POST /api/v1/guardians/g1/pay-link/replace': () => json(second),
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderApp(<FamilyLinkDialog guardianId="g1" name="Mrs Adeyemi" onClose={() => {}} />);
    expect(await screen.findByLabelText('Payment link')).toHaveValue(first.url);
    await userEvent.click(screen.getByRole('button', { name: 'Replace link' }));
    await vi.waitFor(() => expect(screen.getByLabelText('Payment link')).toHaveValue(second.url));
  });
});
