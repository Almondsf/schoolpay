import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, setAccessToken } from '../../api/client';
import { AuthProvider } from '../../auth/AuthContext';
import { json, me, mockApi, signedIn } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ConsoleApp } from '../ConsoleApp';

const admin = me({ capabilities: ['school.view', 'school.manage', 'students.view', 'students.manage', 'staff.view'] });
const term = { id: 't1', session_id: 's1', number: 1, label: 'First term', start_date: '2026-09-07', end_date: '2026-12-11', is_current: true };
const base = {
  'POST /api/v1/auth/refresh': () => json(signedIn(admin)),
  'GET /api/v1/terms/current': () => json({ ...term, session_name: '2026/2027' }),
  'GET /api/v1/academic-sessions': () => json([{ id: 's1', name: '2026/2027', start_date: '2026-09-01', end_date: '2027-07-31', terms: [term] }]),
  'GET /api/v1/class-levels': () =>
    json([{ id: 'l1', name: 'JSS 1', rank: 1, is_active: true, arms: [{ id: 'a1', name: 'A', is_active: true }] }]),
};

function renderAt(path: string) {
  return renderApp(
    <Routes>
      <Route path="/console/*" element={<AuthProvider><ConsoleApp /></AuthProvider>} />
    </Routes>,
    path,
  );
}

const report = (overrides: Record<string, unknown>) => ({
  dry_run: true, total_rows: 3, students_created: 2, students_updated: 0, students_skipped: 0, guardians_created: 1,
  guardian_links_created: 2, preview: [{ row: 2, admission_number: 'A1', name: 'Ada Obi', class_name: 'JSS 1A', guardians: 1, action: 'create' }],
  errors: [{ row: 4, column: 'Class', message: "Class 'JSS 9' doesn't exist." }], ...overrides,
});

describe('students', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it('lists students with their class for the current term', async () => {
    const calls = mockApi({
      ...base,
      'GET /api/v1/students': () =>
        json({
          items: [
            { id: 'st1', admission_number: 'A1', full_name: 'Ada Obi', first_name: 'Ada', last_name: 'Obi', gender: 'female', status: 'active',
              placement: { arm_id: 'a1', arm_name: 'A', class_level_id: 'l1', class_level_name: 'JSS 1' } },
            { id: 'st2', admission_number: 'A2', full_name: 'Tunde Bello', first_name: 'Tunde', last_name: 'Bello', gender: null, status: 'active', placement: null },
          ],
          total: 2, limit: 50, offset: 0,
        }),
    });
    renderAt('/console/students');
    const table = await screen.findByRole('table');
    expect(within(table).getByText('JSS 1 A')).toBeInTheDocument();
    expect(within(table).getByText('Not placed')).toBeInTheDocument();
    const listCall = calls.find((c) => c.url.includes('/api/v1/students?'))!;
    expect(new URL(listCall.url).searchParams.get('term_id')).toBe('t1');
  });

  it("prints a class's pay cards, starting from the class being viewed", async () => {
    const bursar = me({ capabilities: ['school.view', 'students.view', 'payments.record'] });
    const calls = mockApi({
      ...base,
      'POST /api/v1/auth/refresh': () => json(signedIn(bursar)),
      'GET /api/v1/students': () => json({ items: [], total: 0, limit: 50, offset: 0 }),
      'GET /api/v1/pay-cards': () => new Response('pdf', { headers: { 'Content-Type': 'application/pdf' } }),
    });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();

    renderAt('/console/students?class=l1');
    expect(screen.queryByRole('button', { name: 'Add student' })).not.toBeInTheDocument(); // can't manage pupils
    await userEvent.click(await screen.findByRole('button', { name: 'Print pay cards' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('combobox')).toHaveValue('l1');
    await userEvent.click(within(dialog).getByRole('button', { name: /Download cards/ }));
    await vi.waitFor(() => expect(saved).toEqual(['pay-cards-jss-1.pdf']));
    const asked = new URL(calls.find((c) => c.url.includes('/api/v1/pay-cards'))!.url);
    expect(asked.searchParams.get('class_level_id')).toBe('l1');
  });

  it('opens filtered from the school overview links', async () => {
    const calls = mockApi({ ...base, 'GET /api/v1/students': () => json({ items: [], total: 0, limit: 50, offset: 0 }) });
    renderAt('/console/students?show=parent&class=l1');
    expect(await screen.findByLabelText('Show')).toHaveValue('parent');
    const query = new URL(calls.find((c) => c.url.includes('/api/v1/students?'))!.url).searchParams;
    expect(query.get('missing')).toBe('parent');
    expect(query.get('class_level_id')).toBe('l1');
    expect(query.get('unplaced')).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText('Show'), 'unplaced');
    await screen.findByLabelText('Show');
    const last = new URL(calls.filter((c) => c.url.includes('/api/v1/students?')).at(-1)!.url).searchParams;
    expect(last.get('unplaced')).toBe('true');
    expect(last.get('missing')).toBeNull();
  });

  it('previews an import with row problems, then imports', async () => {
    // jsdom's FormData/File can't pass through Node's Request, so stop at the client boundary and
    // check the exact multipart form the page builds
    mockApi(base);
    const forms: FormData[] = [];
    const post = vi.spyOn(api, 'POST').mockImplementation((async (_path: string, options: { body: unknown; bodySerializer: (b: unknown) => FormData }) => {
      const form = options.bodySerializer(options.body);
      forms.push(form);
      const data = report({ dry_run: form.get('dry_run') === 'true' });
      return { data, response: new Response(null, { status: 200 }) };
    }) as unknown as typeof api.POST);
    renderAt('/console/students/import');
    await userEvent.upload(await screen.findByLabelText('Spreadsheet'), new File(['x'], 'students.xlsx'));
    await userEvent.click(screen.getByLabelText(/Parents agreed/));
    await userEvent.click(screen.getByRole('button', { name: 'Check file' }));
    expect(await screen.findByText(/Row 4: Class 'JSS 9' doesn't exist/)).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('Ada Obi')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 students' }));
    expect(await screen.findByRole('heading', { name: 'Import finished' })).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith('/api/v1/students/import', expect.anything());
    expect(forms.map((f) => f.get('dry_run'))).toEqual(['true', 'false']);
    expect(forms[1]!.get('consent_confirmed')).toBe('true');
    expect(forms[1]!.get('mode')).toBe('skip');
    expect((forms[1]!.get('file') as File).name).toBe('students.xlsx');
    post.mockRestore();
  });

  it('asks for the calendar first when there is no current term', async () => {
    mockApi({ ...base, 'GET /api/v1/terms/current': () => json(null) });
    renderAt('/console/students');
    expect(await screen.findByText(/school calendar/)).toBeInTheDocument();
  });
});
