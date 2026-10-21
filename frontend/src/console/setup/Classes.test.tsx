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
const standard = (have: string[] = []) =>
  [
    ['Preparatory', ['Creche', 'Nursery 1']],
    ['Primary', ['Primary 1', 'Primary 2']],
    ['Junior secondary', ['JSS 1']],
  ].flatMap(([group, names]) => (names as string[]).map((name) => ({ group, name, exists: have.includes(name) })));

function renderClasses() {
  return renderApp(
    <Routes>
      <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
    </Routes>,
    '/console/classes',
  );
}

describe('setting up classes', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('starts a new school with the usual classes ticked, and creates what is left ticked', async () => {
    const calls = mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
      'GET /api/v1/class-levels': () => json([]),
      'GET /api/v1/class-levels/standard': () => json(standard()),
      'POST /api/v1/class-levels/batch': () => json([], 201),
    });
    renderClasses();
    expect(await screen.findByRole('heading', { name: 'Set up your classes' })).toBeInTheDocument();
    expect(await screen.findByRole('checkbox', { name: 'Creche' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Create 5 classes' })).toBeInTheDocument();

    // No preparatory classes here; one of their own; two arms each
    await userEvent.click(screen.getAllByRole('button', { name: 'None' })[0]!);
    await userEvent.type(screen.getByPlaceholderText('Class name'), 'Year 13');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await userEvent.clear(screen.getByLabelText(/Arms in each class/));
    await userEvent.type(screen.getByLabelText(/Arms in each class/), 'A, B');
    await userEvent.click(screen.getByRole('button', { name: 'Create 4 classes' }));

    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/class-levels/batch'))).toBe(true));
    const sent = calls.find((c) => c.url.endsWith('/class-levels/batch'))!;
    expect(await sent.json()).toEqual({
      classes: ['Primary 1', 'Primary 2', 'JSS 1', 'Year 13'].map((name) => ({ name, arms: ['A', 'B'] })),
    });
  });

  it('offers a school with classes only the ones it is missing, none ticked', async () => {
    mockApi({
      'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
      'GET /api/v1/class-levels': () =>
        json([{ id: 'l1', name: 'Primary 1', rank: 1, is_active: true, arms: [{ id: 'a1', name: 'A', is_active: true }] }]),
      'GET /api/v1/class-levels/standard': () => json(standard(['Primary 1', 'Primary 2', 'JSS 1'])),
    });
    renderClasses();
    await userEvent.click(await screen.findByRole('button', { name: 'Add from the usual list' }));
    expect(await screen.findByRole('checkbox', { name: 'Creche' })).not.toBeChecked();
    expect(screen.queryByRole('checkbox', { name: 'Primary 2' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create 0 classes' })).toBeDisabled();
  });
});
