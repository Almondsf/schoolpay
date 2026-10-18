import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, StatusBadge } from '../../lib/ui';
import { useClassLevels, useCurrentTerm, useSessions } from '../hooks';

const PAGE = 50;
type InvoiceStatus = 'unpaid' | 'part_paid' | 'paid' | 'void' | 'outstanding';

export function Billing() {
  const { can } = useAuth();
  const sessions = useSessions();
  const current = useCurrentTerm();
  const levels = useClassLevels();
  const [termId, setTermId] = useState('');
  const [status, setStatus] = useState<InvoiceStatus | ''>('');
  const [levelId, setLevelId] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [generating, setGenerating] = useState(false);
  const term = termId || current.data?.id || '';

  const summary = useQuery({
    queryKey: ['billing-summary', term],
    enabled: !!term,
    queryFn: async () => unwrap(await api.GET('/api/v1/terms/{term_id}/billing-summary', { params: { path: { term_id: term } } })),
  });
  const invoices = useQuery({
    queryKey: ['invoices', term, status, levelId, search, offset],
    enabled: !!term,
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/invoices', {
          params: {
            query: { term_id: term, status: status || undefined, class_level_id: levelId || undefined, search: search || undefined, limit: PAGE, offset },
          },
        }),
      ),
  });
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setOffset(0);
  };

  if (current.isPending) return <Loading />;
  if (!term) return <Empty>Set up the school calendar first.</Empty>;
  const s = summary.data;

  return (
    <>
      <PageHeader
        title="Billing"
        subtitle="Invoices for the term and what's still owed."
        actions={can('invoices.issue') && <button onClick={() => setGenerating(true)}>Generate invoices</button>}
      />
      <div className="filters">
        <select aria-label="Term" value={term} onChange={(e) => reset(setTermId)(e.target.value)}>
          {(sessions.data ?? []).flatMap((ses) =>
            ses.terms.map((t) => (
              <option key={t.id} value={t.id}>
                {ses.name} · {t.label}
                {t.is_current ? ' (current)' : ''}
              </option>
            )),
          )}
        </select>
      </div>
      {s && (
        <div className="stats">
          <Stat label="Billed" value={formatKobo(s.billed_kobo)} note={`${s.invoices} invoices`} />
          <Stat label="Paid" value={formatKobo(s.paid_kobo)} />
          <Stat label="Outstanding" value={formatKobo(s.outstanding_kobo)} tone={s.outstanding_kobo > 0 ? 'warn' : 'ok'} />
          <Stat
            label="Not yet invoiced"
            value={String(Math.max(0, s.students_enrolled - s.students_invoiced))}
            note={`of ${s.students_enrolled} students`}
          />
        </div>
      )}
      <div className="filters">
        <input type="search" placeholder="Student name, admission or invoice number" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        <select aria-label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value as InvoiceStatus | '')}>
          <option value="">Any status</option>
          <option value="outstanding">Owing</option>
          <option value="unpaid">Unpaid</option>
          <option value="part_paid">Part paid</option>
          <option value="paid">Paid</option>
          <option value="void">Void</option>
        </select>
        <select aria-label="Class" value={levelId} onChange={(e) => reset(setLevelId)(e.target.value)}>
          <option value="">All classes</option>
          {(levels.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      </div>
      {invoices.isPending ? (
        <Loading />
      ) : invoices.isError ? (
        <ErrorAlert error={invoices.error} />
      ) : invoices.data.items.length === 0 ? (
        <Empty>No invoices match.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Student</th>
                <th className="num">Total</th>
                <th className="num">Paid</th>
                <th className="num">Balance</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {invoices.data.items.map((i) => (
                <tr key={i.id}>
                  <td>
                    <Link to={`/console/invoices/${i.id}`}>#{i.number}</Link>
                  </td>
                  <td>
                    <Link to={`/console/students/${i.student.id}`}>{i.student.full_name}</Link>
                    <div className="muted small">{i.student.admission_number}</div>
                  </td>
                  <td className="num">{formatKobo(i.total_kobo)}</td>
                  <td className="num">{formatKobo(i.paid_kobo)}</td>
                  <td className="num">{formatKobo(i.balance_kobo)}</td>
                  <td>
                    <StatusBadge status={i.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={invoices.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {generating && <Generate termId={term} onClose={() => setGenerating(false)} />}
    </>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'ok' | 'warn' }) {
  return (
    <div className={`card stat ${tone ?? ''}`}>
      <div className="muted small">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="muted small">{note}</div>}
    </div>
  );
}

function Generate({ termId, onClose }: { termId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [dueDate, setDueDate] = useState('');
  const run = useMutation({
    mutationFn: async (dryRun: boolean) =>
      unwrap(
        await api.POST('/api/v1/terms/{term_id}/invoices/generate', {
          params: { path: { term_id: termId } },
          body: { dry_run: dryRun, due_date: dueDate || null },
        }),
      ),
    onSuccess: (report) => {
      if (!report.dry_run) {
        void queryClient.invalidateQueries({ queryKey: ['invoices'] });
        void queryClient.invalidateQueries({ queryKey: ['billing-summary'] });
      }
    },
  });
  const report: Schemas['GenerationOut'] | undefined = run.data;
  const finished = report && !report.dry_run;

  return (
    <Modal title="Generate invoices" onClose={onClose}>
      <p>
        Every current student placed in a class this term gets one invoice: their class's fees, optional items they've signed up for, and their
        discounts. Students who already have an invoice for the term are skipped, so this is safe to run again after adding students.
      </p>
      <Field label="Payment due by (optional)">
        <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} disabled={!!finished} />
      </Field>
      <ErrorAlert error={run.error} />
      {report && (
        <div className="card inset">
          <strong>
            {finished ? 'Done: ' : 'Preview: '}
            {report.invoices} invoices totalling {formatKobo(report.total_kobo)}
          </strong>
          {report.discounts_kobo > 0 && <div>Including {formatKobo(report.discounts_kobo)} of discounts.</div>}
          {report.already_invoiced > 0 && <div className="muted">{report.already_invoiced} students already have an invoice.</div>}
          {report.no_fees_set.length > 0 && <div className="error-text">No fees set for: {report.no_fees_set.join(', ')}. Those students are skipped.</div>}
          {report.by_class.length > 0 && (
            <table className="compact">
              <tbody>
                {report.by_class.map((c) => (
                  <tr key={c.class_level_id}>
                    <td>{c.class_name}</td>
                    <td className="num">{c.invoices}</td>
                    <td className="num">{formatKobo(c.total_kobo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          {finished ? 'Close' : 'Cancel'}
        </button>
        {!finished && (
          <>
            <button className="secondary" onClick={() => run.mutate(true)} disabled={run.isPending}>
              Preview
            </button>
            <button onClick={() => run.mutate(false)} disabled={run.isPending || !report || report.invoices === 0}>
              Generate {report ? report.invoices : ''} invoices
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
