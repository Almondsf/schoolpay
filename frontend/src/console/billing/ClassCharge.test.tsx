import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { json, mockApi } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { ClassCharge } from './ClassCharge';

const levels = [
  { id: 'l1', name: 'SSS 2', rank: 11, is_active: true, arms: [] },
  { id: 'l2', name: 'SSS 3', rank: 12, is_active: true, arms: [] },
];
const students = [
  { student_id: 's1', full_name: 'Ada Eze', admission_number: 'A1', class_name: 'SSS 3 Science', billed: true, excluded: false },
  { student_id: 's2', full_name: 'Bayo Ojo', admission_number: 'A2', class_name: 'SSS 3 Arts', billed: true, excluded: false },
  { student_id: 's3', full_name: 'Chidi Obi', admission_number: 'A3', class_name: 'SSS 3 Arts', billed: false, excluded: false },
];

describe('add a fee to classes', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('previews the pupils, leaves out the unticked one, and applies', async () => {
    const bodies: Record<string, unknown>[] = [];
    mockApi({
      'GET /api/v1/class-levels': () => json(levels),
      'GET /api/v1/fee-items': () => json([]),
      'POST /api/v1/terms/t1/class-charges': async (request) => {
        const body = (await request.json()) as { dry_run: boolean; excluded_student_ids: string[] };
        bodies.push(body);
        return json({
          dry_run: body.dry_run, item_name: 'WAEC', amount_kobo: 2_500_000, charged_now: 1, total_kobo: 5_000_000,
          students: students.map((s) => ({ ...s, excluded: body.excluded_student_ids.includes(s.student_id) })),
        });
      },
    });
    renderApp(<ClassCharge termId="t1" onClose={() => {}} />);
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText("New fee's name"), 'WAEC');
    await userEvent.type(within(dialog).getByLabelText('Amount per pupil (₦)'), '25000');
    await userEvent.click(await within(dialog).findByLabelText('SSS 3'));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Preview' }));

    expect(await within(dialog).findByText(/3 of 3 pupils will pay WAEC: ₦75,000.00/)).toBeInTheDocument();
    expect(within(dialog).getByText(/not billed yet/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /Bayo Ojo/ }));
    expect(within(dialog).getByText(/2 of 3 pupils will pay WAEC: ₦50,000.00/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Add to 2 pupils' }));

    expect(await within(dialog).findByText(/1 bills were updated now/)).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ dry_run: true, new_item_name: 'WAEC', amount_kobo: 2_500_000, class_level_ids: ['l2'] });
    expect(bodies[1]).toMatchObject({ dry_run: false, excluded_student_ids: ['s2'] });
  });
});
