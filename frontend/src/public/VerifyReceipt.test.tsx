import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { error, json, mockApi } from '../test/fetchMock';
import { renderApp } from '../test/render';
import { VerifyReceipt } from './VerifyReceipt';

const CODE = 'lZ6mmnLjB6eCni6vi21qiA';
const genuine = { school_name: 'Demo Model College', receipt_number: 17, amount_kobo: 7_550_050, paid_on: '2026-10-18T13:52:00Z', status: 'valid', student_initials: 'F.A.' };

function renderAt(path: string) {
  return renderApp(
    <Routes>
      <Route path="/verify/:code" element={<VerifyReceipt />} />
    </Routes>,
    path,
  );
}

describe('receipt verification', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('confirms a genuine receipt with only initials', async () => {
    mockApi({ [`GET /api/v1/public/receipts/${CODE}`]: () => json(genuine) });
    renderAt(`/verify/${CODE}`);
    expect(await screen.findByText('Genuine receipt')).toBeInTheDocument();
    expect(screen.getByText('Demo Model College')).toBeInTheDocument();
    expect(screen.getByText('F.A.')).toBeInTheDocument();
    expect(screen.getByText(/75,500\.50/)).toBeInTheDocument();
  });

  it('flags a reversed payment', async () => {
    mockApi({ [`GET /api/v1/public/receipts/${CODE}`]: () => json({ ...genuine, status: 'reversed' }) });
    renderAt(`/verify/${CODE}`);
    expect(await screen.findByText('This payment was reversed')).toBeInTheDocument();
  });

  it('calls an unknown code not genuine', async () => {
    mockApi({ [`GET /api/v1/public/receipts/${CODE}`]: () => error(404, 'not_found') });
    renderAt(`/verify/${CODE}`);
    expect(await screen.findByText('Not genuine')).toBeInTheDocument();
  });

  it('rejects a malformed code without asking the API', async () => {
    const calls = mockApi({});
    renderAt('/verify/bad');
    expect(await screen.findByText('Not genuine')).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });
});
