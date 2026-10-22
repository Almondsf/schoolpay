import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { formatDateTime, humanize } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Notice, PageHeader, Pager, fieldError } from '../../lib/ui';

type Kind = Schemas['MessageKind'];
type Status = Schemas['MessageStatus'];
type Template = Schemas['TemplateOut'];
type ReminderSettings = Schemas['ReminderSettingsIO'];
const PAGE = 50;

const KIND_LABELS: Record<Kind, string> = {
  receipt: 'Payment receipt',
  fee_notice: 'New bill',
  reminder: 'Reminder',
  call_reminder: 'Reminder call',
  call_receipt: 'Payment call',
};
const STATUS_TONE: Record<Status, string> = {
  sent: 'ok',
  queued: 'warn',
  failed: 'danger',
  skipped: '',
};
const SKIP_REASONS: Record<string, string> = {
  opted_out: 'Parent turned off reminders',
  paid: 'Already paid',
  too_old: 'Too late to send',
  no_contact: 'No phone or email',
  calls_off: 'Calls turned off',
  no_recipient: 'No parent on record',
};

export function Messages() {
  const [tab, setTab] = useState<'log' | 'wording' | 'schedule'>('log');
  const tabs = [
    ['log', 'Sent messages'],
    ['wording', 'Wording'],
    ['schedule', 'Reminder schedule'],
  ] as const;
  return (
    <>
      <PageHeader
        title="Messages"
        subtitle="Receipts, new bills and reminders go to each child's main parent by WhatsApp, then text message, then email."
      />
      <div className="tabs" role="tablist">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? 'tab active' : 'tab'}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'log' ? <MessageLog /> : tab === 'wording' ? <Wording /> : <Schedule />}
    </>
  );
}

function MessageLog() {
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState<Kind | ''>('');
  const [status, setStatus] = useState<Status | ''>('');
  const messages = useQuery({
    queryKey: ['messages', kind, status, offset],
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/messages', {
          params: {
            query: {
              kind: kind || undefined,
              status: status || undefined,
              limit: PAGE,
              offset,
            },
          },
        }),
      ),
  });
  return (
    <>
      <div className="filters">
        <select aria-label="Kind" value={kind} onChange={(e) => (setKind(e.target.value as Kind | ''), setOffset(0))}>
          <option value="">All kinds</option>
          {Object.entries(KIND_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select aria-label="Status" value={status} onChange={(e) => (setStatus(e.target.value as Status | ''), setOffset(0))}>
          <option value="">Any status</option>
          {(['sent', 'queued', 'skipped', 'failed'] as const).map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </select>
      </div>
      {messages.isPending ? (
        <Loading />
      ) : messages.isError ? (
        <ErrorAlert error={messages.error} />
      ) : messages.data.items.length === 0 ? (
        <Empty>No messages yet. They appear here as payments come in, bills go out and reminders fall due.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>To</th>
                <th>Kind</th>
                <th>Status</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {messages.data.items.map((m) => (
                <tr key={m.id}>
                  <td className="nowrap">{formatDateTime(m.sent_at ?? m.created_at)}</td>
                  <td>
                    {m.guardian_name}
                    {m.student_id && m.student_name && (
                      <div className="small">
                        <Link to={`/console/students/${m.student_id}`}>{m.student_name}</Link>
                      </div>
                    )}
                  </td>
                  <td>{KIND_LABELS[m.kind]}</td>
                  <td>
                    <span className={`badge ${STATUS_TONE[m.status]}`}>{humanize(m.status)}</span>
                    {m.channel && <div className="muted small">{m.channel === 'sms' ? 'Text message' : m.channel === 'voice' ? 'Phone call' : humanize(m.channel)}</div>}
                    {m.skip_reason && <div className="muted small">{SKIP_REASONS[m.skip_reason] ?? humanize(m.skip_reason)}</div>}
                    {m.error && m.status !== 'sent' && <div className="error-text small">{m.error}</div>}
                  </td>
                  <td className="small">{m.body || <span className="muted">Not sent yet</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={messages.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
    </>
  );
}

function Wording() {
  const templates = useQuery({
    queryKey: ['message-templates'],
    queryFn: async () => unwrap(await api.GET('/api/v1/message-templates')),
  });
  if (templates.isPending) return <Loading />;
  if (templates.isError) return <ErrorAlert error={templates.error} />;
  return (
    <>
      <Notice>
        This wording is used for text messages and email. WhatsApp only allows wording approved in advance, so WhatsApp messages use the
        platform's standard wording with the same details.
      </Notice>
      {templates.data.templates.map((t) => (
        <TemplateEditor key={t.kind} template={t} placeholders={templates.data.placeholders} />
      ))}
    </>
  );
}

function TemplateEditor({ template, placeholders }: { template: Template; placeholders: Record<string, string> }) {
  const queryClient = useQueryClient();
  const [body, setBody] = useState(template.body);
  const [preview, setPreview] = useState<{ text: string; pages: number } | null>(null);
  useEffect(() => setBody(template.body), [template.body]);

  const showPreview = useMutation({
    mutationFn: async (text: string) =>
      unwrap(
        await api.POST('/api/v1/message-templates/preview', {
          body: { body: text },
        }),
      ),
    onSuccess: (data) => setPreview({ text: data.text, pages: data.sms_pages }),
  });
  const save = useMutation({
    mutationFn: async (text: string | null) =>
      unwrap(
        await api.PUT('/api/v1/message-templates/{kind}', {
          params: { path: { kind: template.kind } },
          body: { body: text },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['message-templates'] }),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(body);
  };
  const changed = body.trim() !== template.body;
  return (
    <form className="card section" onSubmit={submit} aria-label={KIND_LABELS[template.kind]}>
      <h2>
        {KIND_LABELS[template.kind]} {template.is_custom && <span className="badge">Your wording</span>}
      </h2>
      <Field
        label="Message"
        hint={`${body.length} characters. One text message is 160.`}
        error={fieldError(save.error ?? showPreview.error, 'body')}
      >
        <textarea rows={4} value={body} maxLength={640} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <details>
        <summary className="small">Details you can include</summary>
        <ul className="small">
          {Object.entries(placeholders).map(([name, description]) => (
            <li key={name}>
              <button type="button" className="secondary small" onClick={() => setBody((b) => `${b}{${name}}`)}>
                {`{${name}}`}
              </button>{' '}
              {description}
            </li>
          ))}
        </ul>
      </details>
      {preview && (
        <div className="alert info" role="status" aria-label="Preview">
          <div>{preview.text}</div>
          <div className="small muted">
            By SMS (₦ shown as NGN): {preview.pages} {preview.pages === 1 ? 'page' : 'pages'} per parent, each billed separately.
          </div>
        </div>
      )}
      <ErrorAlert error={[save.error, showPreview.error].find((e) => e && !fieldError(e, 'body')) ?? null} />
      <div className="row">
        <button type="button" className="secondary" onClick={() => showPreview.mutate(body)} disabled={showPreview.isPending}>
          Preview
        </button>
        {template.is_custom && (
          <button type="button" className="secondary" onClick={() => save.mutate(null)} disabled={save.isPending}>
            Use the standard wording
          </button>
        )}
        <button type="submit" disabled={!changed || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  );
}

function parseDays(input: string): number[] {
  return [
    ...new Set(
      input
        .split(/[\s,]+/)
        .filter(Boolean)
        .map(Number),
    ),
  ].filter((n) => Number.isInteger(n));
}

function Schedule() {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ['reminder-settings'],
    queryFn: async () => unwrap(await api.GET('/api/v1/reminder-settings')),
  });
  const [form, setForm] = useState<
    | (Omit<ReminderSettings, 'days_before_due' | 'days_after_due'> & {
        before: string;
        after: string;
      })
    | null
  >(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (current.data) {
      const { days_before_due, days_after_due, ...rest } = current.data;
      setForm({
        ...rest,
        before: days_before_due.join(', '),
        after: days_after_due.join(', '),
      });
    }
  }, [current.data]);
  const save = useMutation({
    mutationFn: async (body: ReminderSettings) => unwrap(await api.PUT('/api/v1/reminder-settings', { body })),
    onSuccess: () => {
      setSaved(true);
      return queryClient.invalidateQueries({ queryKey: ['reminder-settings'] });
    },
  });
  if (current.isPending || !form) return current.isError ? <ErrorAlert error={current.error} /> : <Loading />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    const { before, after, ...rest } = form;
    save.mutate({
      ...rest,
      days_before_due: parseDays(before),
      days_after_due: parseDays(after),
    });
  };
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm({ ...form, [key]: value });
  return (
    <form className="card section page--narrow" onSubmit={submit} aria-label="Reminder schedule">
      <label className="checkbox">
        <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
        Send fee reminders automatically
      </label>
      <Field
        label="Days before the due date"
        hint="For example 7, 1. Leave empty for none."
        error={fieldError(save.error, 'days_before_due')}
      >
        <input value={form.before} onChange={(e) => set('before', e.target.value)} disabled={!form.enabled} />
      </Field>
      <Field
        label="Days after the due date, while still owed"
        hint="For example 3, 14, 30."
        error={fieldError(save.error, 'days_after_due')}
      >
        <input value={form.after} onChange={(e) => set('after', e.target.value)} disabled={!form.enabled} />
      </Field>
      <Field label="Time of day" hint="Lagos time. Nothing is ever sent between 8pm and 7am.">
        <select value={form.send_hour} onChange={(e) => set('send_hour', Number(e.target.value))} disabled={!form.enabled}>
          {Array.from({ length: 13 }, (_, i) => i + 7).map((h) => (
            <option key={h} value={h}>
              {h === 12 ? '12 noon' : h < 12 ? `${h}am` : `${h - 12}pm`}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Most reminders per bill" hint="Parents are never sent more than this for one bill.">
        <input
          type="number"
          min={0}
          max={10}
          value={form.max_per_invoice}
          onChange={(e) => set('max_per_invoice', Number(e.target.value))}
          disabled={!form.enabled}
        />
      </Field>
      <Field label="At least this many days between reminders">
        <input
          type="number"
          min={1}
          max={30}
          value={form.min_gap_days}
          onChange={(e) => set('min_gap_days', Number(e.target.value))}
          disabled={!form.enabled}
        />
      </Field>
      <fieldset>
        <legend>Phone calls</legend>
        <label className="checkbox">
          <input type="checkbox" checked={form.calls_enabled} onChange={(e) => set('calls_enabled', e.target.checked)} />
          Phone parents who prefer calls
        </label>
        <p className="hint">
          For parents who don't read messages easily. Mark them on each pupil's page (English or Pidgin). They're called about overdue
          bills, reading out the amount and the child's account number slowly, twice, and when a payment arrives. Calls happen between
          9am and 7pm and cost the school per minute.
        </p>
        <Field label="Most calls per bill">
          <input
            type="number"
            min={0}
            max={5}
            value={form.max_calls_per_invoice}
            onChange={(e) => set('max_calls_per_invoice', Number(e.target.value))}
            disabled={!form.calls_enabled}
          />
        </Field>
      </fieldset>
      <ErrorAlert
        error={save.error && !fieldError(save.error, 'days_before_due') && !fieldError(save.error, 'days_after_due') ? save.error : null}
      />
      {saved && <p className="success-text">Saved.</p>}
      <div className="row">
        <button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save schedule'}
        </button>
      </div>
    </form>
  );
}
