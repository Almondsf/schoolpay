import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { json, mockApi } from '../../test/fetchMock';
import { renderApp } from '../../test/render';
import { Messages } from './Messages';

const page = (items: unknown[]) => ({
  items,
  total: items.length,
  limit: 50,
  offset: 0,
});
const message = (overrides: Record<string, unknown>) => ({
  id: 'm1',
  kind: 'receipt',
  status: 'sent',
  channel: 'whatsapp',
  student_id: 's1',
  student_name: 'Tolu Adeyemi',
  guardian_name: 'Mrs Adeyemi',
  body: 'Dear Mrs Adeyemi, we received ₦50,000.00',
  skip_reason: '',
  error: '',
  created_at: '2026-10-01T09:00:00Z',
  sent_at: '2026-10-01T09:00:05Z',
  ...overrides,
});
const reminder = {
  kind: 'reminder',
  body: 'Default reminder {balance}',
  is_custom: false,
  default_body: 'Default reminder {balance}',
};
const templates = {
  templates: [
    {
      kind: 'receipt',
      body: 'Receipt {amount}',
      is_custom: false,
      default_body: 'Receipt {amount}',
    },
    {
      kind: 'fee_notice',
      body: 'Bill {amount}',
      is_custom: false,
      default_body: 'Bill {amount}',
    },
    reminder,
  ],
  placeholders: {
    balance: "What's still owed",
    parent_name: "The parent's name",
  },
};

describe('messages page', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists messages with their channel or why they were skipped', async () => {
    mockApi({
      'GET /api/v1/messages': () =>
        json(
          page([
            message({}),
            message({
              id: 'm2',
              kind: 'reminder',
              status: 'skipped',
              channel: null,
              body: '',
              skip_reason: 'opted_out',
            }),
          ]),
        ),
    });
    renderApp(<Messages />);
    expect(await screen.findByText('Dear Mrs Adeyemi, we received ₦50,000.00')).toBeInTheDocument();
    expect(screen.getByText('Whatsapp')).toBeInTheDocument();
    expect(screen.getByText('Parent turned off reminders')).toBeInTheDocument();
  });

  it('previews and saves the school wording, showing a bad placeholder', async () => {
    let saved: unknown;
    mockApi({
      'GET /api/v1/messages': () => json(page([])),
      'GET /api/v1/message-templates': () => json(templates),
      'POST /api/v1/message-templates/preview': async (request) => {
        const { body } = (await request.json()) as { body: string };
        return body.includes('{oops}')
          ? json(
              {
                error: {
                  code: 'invalid',
                  message: 'Unknown placeholder: {oops}.',
                  field: 'body',
                },
              },
              400,
            )
          : json({ text: body.replace('{balance}', '₦55,000.00') });
      },
      'PUT /api/v1/message-templates/reminder': async (request) => {
        saved = await request.json();
        return json({
          ...reminder,
          body: (saved as { body: string }).body,
          is_custom: true,
        });
      },
    });
    renderApp(<Messages />);
    await userEvent.click(await screen.findByRole('tab', { name: 'Wording' }));
    const form = await screen.findByRole('form', { name: 'Reminder' });
    const box = within(form).getByRole('textbox');

    await userEvent.clear(box);
    await userEvent.type(box, 'Owing {{oops}');
    await userEvent.click(within(form).getByRole('button', { name: 'Preview' }));
    expect(await within(form).findByText(/Unknown placeholder/)).toBeInTheDocument();

    await userEvent.clear(box);
    await userEvent.type(box, 'Please pay {{balance}');
    await userEvent.click(within(form).getByRole('button', { name: 'Preview' }));
    expect(await within(form).findByText('Please pay ₦55,000.00')).toBeInTheDocument();
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await vi.waitFor(() => expect(saved).toEqual({ body: 'Please pay {balance}' }));
  });
});
