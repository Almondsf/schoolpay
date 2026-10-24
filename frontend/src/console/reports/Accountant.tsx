import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { errorMessage } from '../../lib/download';
import { formatDate, formatKobo, lagosToday } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal } from '../../lib/ui';
import { useSessions } from '../hooks';
import { useBackgroundExport } from './useBackgroundExport';

type Account = Schemas['JournalAccountOut'];
type JournalFormat = 'xlsx' | 'csv';

/** The general journal for the school's accountant, and the account codes it uses. */
export function Accountant() {
  const { can } = useAuth();
  const today = lagosToday();
  const [start, setStart] = useState(`${today.slice(0, 8)}01`);
  const [end, setEnd] = useState(today);
  const period = { start, end };
  const summary = useQuery({
    queryKey: ['journal', period],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/accounting/journal', { params: { query: period } })),
    enabled: Boolean(start && end),
  });
  const exporting = useBackgroundExport();
  const download = {
    mutate: (format: JournalFormat) => exporting.mutate({ kind: 'journal', ...period, format }),
    isPending: exporting.isPending,
    error: exporting.error,
    // The button that was pressed says so while its file is prepared
    preparing: exporting.isPending ? exporting.variables?.format : undefined,
  };
  return (
    <>
      <p className="muted small">
        Every bill, payment, banking and approved spending as double entries, ready for your accountant or accounting software. Pupils
        appear by admission number only.
      </p>
      <div className="filters">
        <label className="inline-field">
          From <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="inline-field">
          To <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {summary.isPending ? (
        <Loading />
      ) : summary.isError ? (
        <ErrorAlert error={summary.error} />
      ) : (
        <>
          <div className="row between wrap">
            <div className="stats compact">
              <div>
                <div className="muted small">Entries</div>
                <div className="stat-value">{summary.data.entries.toLocaleString()}</div>
              </div>
              <div>
                <div className="muted small">Debits</div>
                <div className="stat-value">{formatKobo(summary.data.debit_kobo)}</div>
              </div>
              <div>
                <div className="muted small">Credits</div>
                <div className="stat-value">{formatKobo(summary.data.credit_kobo)}</div>
              </div>
            </div>
            <div className="row">
              <button className="secondary small" onClick={() => download.mutate('xlsx')} disabled={download.isPending}>
                {download.preparing === 'xlsx' ? 'Preparing…' : 'Download Excel'}
              </button>
              <button className="secondary small" onClick={() => download.mutate('csv')} disabled={download.isPending}>
                {download.preparing === 'csv' ? 'Preparing…' : 'Download CSV'}
              </button>
            </div>
          </div>
          <ErrorAlert error={download.error} />
          {summary.data.unchecked_deposits > 0 && (
            <p className="muted small">
              {summary.data.unchecked_deposits} cash {summary.data.unchecked_deposits === 1 ? 'deposit is' : 'deposits are'} left out until
              a second person checks {summary.data.unchecked_deposits === 1 ? 'it' : 'them'} against the bank statement.
            </p>
          )}
          {summary.data.entries === 0 ? (
            <Empty>
              Nothing recorded between {formatDate(start)} and {formatDate(end)}.
            </Empty>
          ) : (
            <section className="card table-card" aria-label="Account totals">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Code</th>
                      <th>Account</th>
                      <th className="num">Debit</th>
                      <th className="num">Credit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.data.totals.map((t) => (
                      <tr key={t.account.key}>
                        <td>{t.account.code}</td>
                        <td>{t.account.name}</td>
                        <td className="num">{t.debit_kobo ? formatKobo(t.debit_kobo) : ''}</td>
                        <td className="num">{t.credit_kobo ? formatKobo(t.credit_kobo) : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
      <ClosingTheBooks />
      <SessionPack />
      <ChartOfAccounts editable={can('payments.approve')} />
    </>
  );
}

type BookClosing = Schemas['BookClosingOut'];

/** Close a term once it has ended, so its figures can't change; the proprietor can re-open the last one. */
function ClosingTheBooks() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const sessions = useSessions();
  const books = useQuery({
    queryKey: ['books'],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/accounting/books')),
  });
  const [closing, setClosing] = useState<{ id: string; label: string; end: string } | null>(null);
  const [reopening, setReopening] = useState(false);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['books'] });
    void queryClient.invalidateQueries({ queryKey: ['journal'] });
  };
  if (books.isPending || sessions.isPending) return <Loading />;
  if (books.isError) return <ErrorAlert error={books.error} />;
  const today = lagosToday();
  const through = books.data.closed_through;
  const terms = (sessions.data ?? []).flatMap((s) => s.terms.map((t) => ({ id: t.id, label: `${s.name} ${t.label}`, end: t.end_date })));
  const labels = new Map(terms.map((t) => [t.id, t.label]));
  // The next term to close: ended, and after the closed date
  const next = terms
    .filter((t) => t.end < today && (!through || t.end > through))
    .sort((a, b) => a.end.localeCompare(b.end))
    .at(-1);
  return (
    <section className="card section" aria-label="Closing the books">
      <h2>Closing the books</h2>
      <p className="muted small">
        Close a term once it has ended and its figures have been reported. After that nothing can be recorded with a date in it; corrections
        are dated the day they are made.
      </p>
      <p>
        {through ? (
          <>
            The books are closed up to <strong>{formatDate(through)}</strong>.
          </>
        ) : (
          'No term has been closed yet.'
        )}
      </p>
      <div className="row wrap">
        {next && can('payments.approve') && (
          <button className="secondary small" onClick={() => setClosing(next)}>
            Close {next.label}
          </button>
        )}
        {through && can('books.reopen') && (
          <button className="secondary small" onClick={() => setReopening(true)}>
            Re-open the last closed term
          </button>
        )}
      </div>
      {books.data.history.length > 0 && (
        <ul className="small muted">
          {books.data.history.map((h: BookClosing) => (
            <li key={h.id}>
              {formatDate(h.created_at.slice(0, 10))}: {h.action === 'close' ? 'closed' : 're-opened'} {labels.get(h.term_id) ?? 'a term'}
              {h.action === 'close' && typeof h.figures.collected_kobo === 'number' && typeof h.figures.billed_kobo === 'number' && (
                <>
                  {' '}
                  ({formatKobo(h.figures.collected_kobo)} collected of {formatKobo(h.figures.billed_kobo)} billed)
                </>
              )}
              {h.reason && <>: “{h.reason}”</>}
            </li>
          ))}
        </ul>
      )}
      {closing && <CloseTerm term={closing} onClose={() => setClosing(null)} onDone={refresh} />}
      {reopening && <Reopen onClose={() => setReopening(false)} onDone={refresh} />}
    </section>
  );
}

function CloseTerm({
  term,
  onClose,
  onDone,
}: {
  term: { id: string; label: string; end: string };
  onClose: () => void;
  onDone: () => void;
}) {
  const close = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/accounting/terms/{term_id}/close', { params: { path: { term_id: term.id } } })),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title={`Close ${term.label}?`} onClose={onClose}>
      <p>
        Nothing dated on or before {formatDate(term.end)} can be recorded after this: no cash, teller or bank payments, spending, banking or
        drawer counts, and the term’s bills can’t be billed again or voided. Corrections are still possible, dated today.
      </p>
      <p className="muted small">Anything still waiting for a second person’s approval in the term must be decided first.</p>
      <ErrorAlert error={close.error} />
      <div className="row">
        <button onClick={() => close.mutate()} disabled={close.isPending}>
          Close the term
        </button>
        <button className="secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

function Reopen({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const reopen = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/accounting/books/reopen', { body: { reason } })),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title="Re-open the last closed term?" onClose={onClose}>
      <p>Its figures may then change. The reason is kept with the books.</p>
      <Field label="Why">
        <input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorAlert error={reopen.error} />
      <div className="row">
        <button onClick={() => reopen.mutate()} disabled={reopen.isPending || reason.trim().length < 3}>
          Re-open
        </button>
        <button className="secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

/** One workbook that closes a school year: summary, terms, money in and out, debtors, cash book, journal. */
function SessionPack() {
  const sessions = useSessions();
  const today = lagosToday();
  const started = (sessions.data ?? []).filter((s) => s.start_date <= today);
  const [chosen, setChosen] = useState('');
  const sessionId = chosen || [...started].sort((a, b) => b.start_date.localeCompare(a.start_date))[0]?.id || '';
  const exporting = useBackgroundExport();
  const download = {
    mutate: () => exporting.mutate({ kind: 'session_pack', session_id: sessionId || null }),
    isPending: exporting.isPending,
    error: exporting.error,
  };
  if (started.length === 0) return null;
  return (
    <section className="card section" aria-label="End of session">
      <h2>End-of-session pack</h2>
      <p className="muted small">
        One Excel file to close the school year: a summary, each term’s bills, money in and spending, who still owes, the cash book month by
        month and the whole year’s journal. For the current year it runs to today.
      </p>
      <div className="row wrap">
        <select aria-label="School year" value={sessionId} onChange={(e) => setChosen(e.target.value)}>
          {started.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <button className="secondary small" onClick={() => download.mutate()} disabled={download.isPending || !sessionId}>
          {download.isPending ? 'Preparing…' : 'Download the pack'}
        </button>
      </div>
      <ErrorAlert error={download.error} />
    </section>
  );
}

function ChartOfAccounts({ editable }: { editable: boolean }) {
  const accounts = useQuery({
    queryKey: ['journal-accounts'],
    queryFn: async () => unwrap(await api.GET('/api/v1/accounting/accounts')),
  });
  return (
    <section className="card table-card" aria-label="Chart of accounts">
      <div className="card-pad">
        <h2>Chart of accounts</h2>
        <p className="muted small">
          WardPay’s default codes.{' '}
          {editable && 'If your accountant uses different ones, change them here; leave a box empty for the default.'}
        </p>
      </div>
      {accounts.isPending ? (
        <Loading />
      ) : accounts.isError ? (
        <ErrorAlert error={accounts.error} />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th style={{ width: '14%' }}>Code</th>
                <th>Account</th>
                <th>Type</th>
                {editable && <th />}
              </tr>
            </thead>
            <tbody>
              {accounts.data.map((a) => (editable ? <AccountRow key={a.key} account={a} /> : <ReadOnlyRow key={a.key} account={a} />))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReadOnlyRow({ account }: { account: Account }) {
  return (
    <tr>
      <td>{account.code}</td>
      <td>{account.name}</td>
      <td className="muted small">{account.type}</td>
    </tr>
  );
}

function AccountRow({ account }: { account: Account }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState(account.code);
  const [name, setName] = useState(account.name);
  const changed = code.trim() !== account.code || name.trim() !== account.name;
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PUT('/api/v1/accounting/accounts/{key}', {
          params: { path: { key: account.key } },
          // The default is stored as "nothing of the school's own"
          body: {
            code: code.trim() === account.default_code ? '' : code,
            name: name.trim() === account.default_name ? '' : name,
          },
        }),
      ),
    onSuccess: (saved) => {
      setCode(saved.code);
      setName(saved.name);
      void queryClient.invalidateQueries({ queryKey: ['journal-accounts'] });
      void queryClient.invalidateQueries({ queryKey: ['journal'] });
    },
  });
  return (
    <tr>
      <td>
        <input
          aria-label={`Code for ${account.default_name}`}
          value={code}
          placeholder={account.default_code}
          maxLength={20}
          onChange={(e) => setCode(e.target.value)}
        />
      </td>
      <td>
        <input
          aria-label={`Name for ${account.default_name}`}
          value={name}
          placeholder={account.default_name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
        {save.isError && <div className="error-text small">{errorMessage(save.error) ?? 'Could not save.'}</div>}
      </td>
      <td className="muted small">{account.type}</td>
      <td>
        <button className="secondary small" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          Save
        </button>
      </td>
    </tr>
  );
}
