import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { errorMessage, filenameFrom, saveBlob } from '../../lib/download';
import { formatDate, formatKobo, lagosToday } from '../../lib/format';
import { Empty, ErrorAlert, Loading } from '../../lib/ui';

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
  const download = useMutation({
    mutationFn: async (format: JournalFormat) => {
      const { data, error, response } = await api.GET('/api/v1/accounting/journal/export', {
        params: { query: { ...period, format } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not download the journal.');
      saveBlob(data as Blob, filenameFrom(response, `journal.${format}`));
    },
  });
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
                Download Excel
              </button>
              <button className="secondary small" onClick={() => download.mutate('csv')} disabled={download.isPending}>
                Download CSV
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
      <ChartOfAccounts editable={can('payments.approve')} />
    </>
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
