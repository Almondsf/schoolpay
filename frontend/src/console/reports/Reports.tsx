import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { filenameFrom, saveBlob } from '../../lib/download';
import { formatDate, formatKobo, parseNairaToKobo } from '../../lib/format';
import { Empty, ErrorAlert, Loading, PageHeader, Pager } from '../../lib/ui';
import { METHOD_LABELS } from '../Dashboard';
import { useClassLevels } from '../hooks';

type Sort = Schemas['DebtorSort'];
type Format = 'xlsx' | 'pdf';
const PAGE = 50;

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function Reports() {
  const [tab, setTab] = useState<'debtors' | 'collections'>('debtors');
  return (
    <>
      <PageHeader title="Reports" subtitle="Who owes, and money received. Download any of them for Excel or to print." />
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'debtors'}
          className={tab === 'debtors' ? 'tab active' : 'tab'}
          onClick={() => setTab('debtors')}
        >
          Debtors
        </button>
        <button
          role="tab"
          aria-selected={tab === 'collections'}
          className={tab === 'collections' ? 'tab active' : 'tab'}
          onClick={() => setTab('collections')}
        >
          Money received
        </button>
      </div>
      {tab === 'debtors' ? <Debtors /> : <Collections />}
    </>
  );
}

function Downloads({ onDownload, pending, error }: { onDownload: (f: Format) => void; pending: boolean; error: unknown }) {
  return (
    <>
      <div className="row">
        <button className="secondary small" onClick={() => onDownload('xlsx')} disabled={pending}>
          Download Excel
        </button>
        <button className="secondary small" onClick={() => onDownload('pdf')} disabled={pending}>
          Download PDF
        </button>
      </div>
      <ErrorAlert error={error} />
    </>
  );
}

function Debtors() {
  const levels = useClassLevels();
  const [classLevelId, setClassLevelId] = useState('');
  const [search, setSearch] = useState('');
  const [minOwed, setMinOwed] = useState('');
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [sort, setSort] = useState<Sort>('owed');
  const [offset, setOffset] = useState(0);
  const minKobo = minOwed ? parseNairaToKobo(minOwed) : null;
  const filters = {
    class_level_id: classLevelId || undefined,
    search: search || undefined,
    min_owed_kobo: minKobo && minKobo > 0 ? minKobo : undefined,
    overdue_only: overdueOnly || undefined,
    sort,
  };
  const debtors = useQuery({
    queryKey: ['debtors', filters, offset],
    staleTime: 0,
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/reports/debtors', {
          params: { query: { ...filters, limit: PAGE, offset } },
        }),
      ),
  });
  const download = useMutation({
    mutationFn: async (format: Format) => {
      const { data, error, response } = await api.GET('/api/v1/reports/debtors/export', {
        params: { query: { ...filters, format } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not download the list.');
      saveBlob(data as Blob, filenameFrom(response, `debtors.${format}`));
    },
  });
  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setOffset(0);
    };

  return (
    <>
      <div className="filters">
        <input type="search" placeholder="Student or admission number" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        <select aria-label="Class" value={classLevelId} onChange={(e) => reset(setClassLevelId)(e.target.value)}>
          <option value="">All classes</option>
          {(levels.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Owing at least (₦)"
          placeholder="Owing at least ₦"
          inputMode="decimal"
          value={minOwed}
          onChange={(e) => reset(setMinOwed)(e.target.value)}
        />
        <select aria-label="Sort by" value={sort} onChange={(e) => reset(setSort)(e.target.value as Sort)}>
          <option value="owed">Most owed first</option>
          <option value="oldest">Longest overdue first</option>
          <option value="class">By class</option>
          <option value="name">By name</option>
        </select>
        <label className="checkbox">
          <input type="checkbox" checked={overdueOnly} onChange={(e) => reset(setOverdueOnly)(e.target.checked)} />
          Overdue only
        </label>
      </div>
      {debtors.isPending ? (
        <Loading />
      ) : debtors.isError ? (
        <ErrorAlert error={debtors.error} />
      ) : (
        <>
          <div className="row between wrap">
            <p>
              <strong>{debtors.data.total}</strong> students owe <strong>{formatKobo(debtors.data.total_owed_kobo)}</strong>
            </p>
            <Downloads onDownload={(f) => download.mutate(f)} pending={download.isPending} error={download.error} />
          </div>
          {debtors.data.items.length === 0 ? (
            <Empty>Nobody matches. Everyone's paid up, or try other filters.</Empty>
          ) : (
            <div className="card table-card">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Student</th>
                      <th>Class</th>
                      <th className="num">Owed</th>
                      <th>Overdue</th>
                      <th>Parent</th>
                      <th>Reminders</th>
                      <th>Last paid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {debtors.data.items.map((d) => (
                      <tr key={d.student_id}>
                        <td>
                          <Link to={`/console/students/${d.student_id}`}>{d.full_name}</Link>
                          <div className="muted small">
                            {d.admission_number}
                            {d.status !== 'active' && <span className="badge">Left</span>}
                          </div>
                        </td>
                        <td>{d.class_name || <span className="muted">—</span>}</td>
                        <td className="num">{formatKobo(d.owed_kobo)}</td>
                        <td>
                          {d.on_plan && <span className="badge">On a plan</span>}{' '}
                          {d.days_overdue > 0 ? (
                            <span className={`badge ${d.days_overdue > 30 ? 'danger' : 'warn'}`}>{d.days_overdue} days</span>
                          ) : (
                            <span className="muted small">Not yet due</span>
                          )}
                        </td>
                        <td>
                          {d.guardian_name || <span className="muted">No parent on record</span>}
                          {d.guardian_phone && (
                            <div className="small">
                              <a href={`tel:${d.guardian_phone}`}>{d.guardian_phone}</a>
                            </div>
                          )}
                        </td>
                        <td>
                          {d.reminders_off ? (
                            <span className="badge">Turned off</span>
                          ) : (
                            <span className="small">{d.reminders_sent} sent</span>
                          )}
                        </td>
                        <td className="nowrap small">
                          {d.last_paid_at ? formatDate(d.last_paid_at) : <span className="muted">Never</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager total={debtors.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
            </div>
          )}
        </>
      )}
    </>
  );
}

function Collections() {
  const today = new Date();
  const [start, setStart] = useState(isoDate(new Date(today.getFullYear(), today.getMonth(), 1)));
  const [end, setEnd] = useState(isoDate(today));
  const period = { start, end };
  const collections = useQuery({
    queryKey: ['collections', period],
    staleTime: 0,
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/reports/collections', {
          params: { query: period },
        }),
      ),
    enabled: Boolean(start && end),
  });
  const download = useMutation({
    mutationFn: async (format: Format) => {
      const { data, error, response } = await api.GET('/api/v1/reports/collections/export', {
        params: { query: { ...period, format } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not download the report.');
      saveBlob(data as Blob, filenameFrom(response, `money-received.${format}`));
    },
  });
  return (
    <>
      <div className="filters">
        <label className="inline-field">
          From <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="inline-field">
          To <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {collections.isPending ? (
        <Loading />
      ) : collections.isError ? (
        <ErrorAlert error={collections.error} />
      ) : (
        <>
          <div className="row between wrap">
            <div className="stats compact">
              <div>
                <div className="muted small">Received</div>
                <div className="stat-value">{formatKobo(collections.data.received_kobo)}</div>
              </div>
              <div>
                <div className="muted small">Reversed</div>
                <div className="stat-value">{formatKobo(collections.data.reversed_kobo)}</div>
              </div>
              <div>
                <div className="muted small">Net</div>
                <div className="stat-value">{formatKobo(collections.data.net_kobo)}</div>
              </div>
              <div>
                <div className="muted small">Payments</div>
                <div className="stat-value">{collections.data.payments}</div>
              </div>
            </div>
            <Downloads onDownload={(f) => download.mutate(f)} pending={download.isPending} error={download.error} />
          </div>
          {collections.data.by_day.length === 0 ? (
            <Empty>No payments in this period.</Empty>
          ) : (
            <div className="grid-2">
              <div className="card table-card">
                <table>
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th className="num">Payments</th>
                      <th className="num">Net received</th>
                    </tr>
                  </thead>
                  <tbody>
                    {collections.data.by_day.map((d) => (
                      <tr key={d.day}>
                        <td>{formatDate(d.day)}</td>
                        <td className="num">{d.payments}</td>
                        <td className="num">{formatKobo(d.amount_kobo)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="card table-card">
                <table>
                  <thead>
                    <tr>
                      <th>Method</th>
                      <th className="num">Payments</th>
                      <th className="num">Net received</th>
                    </tr>
                  </thead>
                  <tbody>
                    {collections.data.by_method.map((m) => (
                      <tr key={m.method}>
                        <td>{METHOD_LABELS[m.method]}</td>
                        <td className="num">{m.payments}</td>
                        <td className="num">{formatKobo(m.amount_kobo)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

/** The API's error message from a failed download (the body is JSON even though we asked for a file). */
function errorMessage(error: unknown): string | null {
  const message = (error as { error?: { message?: unknown } } | undefined)?.error?.message;
  return typeof message === 'string' ? message : null;
}
