import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, Notice, PageHeader, fieldError } from '../../lib/ui';
import { useSessions, type AcademicSession, type Term } from '../hooks';

export function Calendar() {
  const { can } = useAuth();
  const manage = can('school.manage');
  const sessions = useSessions();
  const [creating, setCreating] = useState(false);
  const [rolling, setRolling] = useState<Term | null>(null);
  const queryClient = useQueryClient();
  const makeCurrent = useMutation({
    mutationFn: async (termId: string) =>
      unwrap(await api.POST('/api/v1/terms/{term_id}/make-current', { params: { path: { term_id: termId } } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['academic-sessions'] });
      void queryClient.invalidateQueries({ queryKey: ['current-term'] });
    },
  });

  const allTerms = (sessions.data ?? []).flatMap((s) => s.terms);
  const current = allTerms.find((t) => t.is_current);

  return (
    <>
      <PageHeader
        title="School calendar"
        subtitle="Sessions run September to July in three terms. Fees are billed per term."
        actions={manage && <button onClick={() => setCreating(true)}>New session</button>}
      />
      <ErrorAlert error={makeCurrent.error} />
      {sessions.isPending ? (
        <Loading />
      ) : sessions.isError ? (
        <ErrorAlert error={sessions.error} />
      ) : sessions.data.length === 0 ? (
        <Empty>No sessions yet. Create the current session to start adding students.</Empty>
      ) : (
        sessions.data.map((session) => (
          <div className="card section" key={session.id}>
            <h2>{session.name}</h2>
            <table>
              <thead>
                <tr>
                  <th>Term</th>
                  <th>Starts</th>
                  <th>Ends</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {session.terms.map((term) => (
                  <tr key={term.id}>
                    <td>
                      {term.label} {term.is_current && <span className="badge ok">Current</span>}
                    </td>
                    <td>{formatDate(term.start_date)}</td>
                    <td>{formatDate(term.end_date)}</td>
                    <td className="actions">
                      {manage && !term.is_current && (
                        <button className="secondary small" onClick={() => makeCurrent.mutate(term.id)} disabled={makeCurrent.isPending}>
                          Make current
                        </button>
                      )}{' '}
                      {manage && current && term.id !== current.id && term.start_date > current.start_date && (
                        <button className="secondary small" onClick={() => setRolling(term)}>
                          Move students here
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
      {creating && <NewSession onClose={() => setCreating(false)} hasCurrent={!!current} />}
      {rolling && current && (
        <RollForward from={current} to={rolling} sessions={sessions.data ?? []} onClose={() => setRolling(null)} />
      )}
    </>
  );
}

function suggestedSession(): { name: string; start: number } {
  const now = new Date();
  const start = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1; // from August, it's the new year
  return { name: `${start}/${start + 1}`, start };
}

function NewSession({ onClose, hasCurrent }: { onClose: () => void; hasCurrent: boolean }) {
  const queryClient = useQueryClient();
  const { name, start } = suggestedSession();
  const [form, setForm] = useState({
    name,
    start_date: `${start}-09-01`,
    end_date: `${start + 1}-07-31`,
    terms: [
      { start_date: `${start}-09-08`, end_date: `${start}-12-12` },
      { start_date: `${start + 1}-01-05`, end_date: `${start + 1}-04-02` },
      { start_date: `${start + 1}-04-20`, end_date: `${start + 1}-07-24` },
    ],
    make_first_term_current: !hasCurrent,
  });
  const create = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/academic-sessions', { body: form })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['academic-sessions'] });
      void queryClient.invalidateQueries({ queryKey: ['current-term'] });
      onClose();
    },
  });
  const setTerm = (index: number, key: 'start_date' | 'end_date', value: string) =>
    setForm({ ...form, terms: form.terms.map((t, i) => (i === index ? { ...t, [key]: value } : t)) });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Modal title="New session" onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorAlert error={create.error} />
        <Field label="Name" hint="Like 2026/2027" error={fieldError(create.error, 'name')}>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} pattern="\d{4}/\d{4}" required />
        </Field>
        <div className="grid-2 tight">
          <Field label="Session starts">
            <input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} required />
          </Field>
          <Field label="Session ends">
            <input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} required />
          </Field>
        </div>
        {form.terms.map((term, i) => (
          <div className="grid-2 tight" key={i}>
            <Field label={`${['First', 'Second', 'Third'][i]} term starts`}>
              <input type="date" value={term.start_date} onChange={(e) => setTerm(i, 'start_date', e.target.value)} required />
            </Field>
            <Field label="Ends">
              <input type="date" value={term.end_date} onChange={(e) => setTerm(i, 'end_date', e.target.value)} required />
            </Field>
          </div>
        ))}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={form.make_first_term_current}
            onChange={(e) => setForm({ ...form, make_first_term_current: e.target.checked })}
          />
          Make the first term the current term
        </label>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={create.isPending}>
            Create session
          </button>
        </div>
      </form>
    </Modal>
  );
}

function RollForward({ from, to, sessions, onClose }: { from: Term; to: Term; sessions: AcademicSession[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const promote = from.session_id !== to.session_id;
  const name = (term: Term) => `${sessions.find((s) => s.id === term.session_id)?.name ?? ''} ${term.label}`;
  const run = useMutation({
    mutationFn: async (dryRun: boolean) =>
      unwrap(
        await api.POST('/api/v1/terms/{term_id}/roll-forward', {
          params: { path: { term_id: to.id } },
          body: { from_term_id: from.id, promote, dry_run: dryRun },
        }),
      ),
    onSuccess: (report) => {
      if (!report.dry_run) void queryClient.invalidateQueries({ queryKey: ['students'] });
    },
  });
  const report = run.data;

  return (
    <Modal title={`Move students to ${name(to)}`} onClose={onClose}>
      <p>
        {promote
          ? `Everyone in ${name(from)} moves up one class. Students in the highest class graduate.`
          : `Everyone in ${name(from)} keeps their class for ${name(to)}.`}{' '}
        Students already placed in {name(to)} are left as they are.
      </p>
      <ErrorAlert error={run.error} />
      {report && (
        <Notice>
          {report.dry_run ? 'Preview: ' : 'Done: '}
          {promote ? `${report.promoted} promoted, ${report.graduated} graduating` : `${report.moved} moved`}
          {report.already_enrolled > 0 && `, ${report.already_enrolled} already placed`}
          {report.unplaced.length > 0 && `, ${report.unplaced.length} need placing by hand`}.
        </Notice>
      )}
      {report && report.unplaced.length > 0 && (
        <ul className="small">
          {report.unplaced.map((u) => (
            <li key={u.student_id}>
              {u.name} ({u.admission_number}): {u.reason}
            </li>
          ))}
        </ul>
      )}
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          {report && !report.dry_run ? 'Close' : 'Cancel'}
        </button>
        {(!report || report.dry_run) && (
          <>
            <button className="secondary" onClick={() => run.mutate(true)} disabled={run.isPending}>
              Preview
            </button>
            <button onClick={() => run.mutate(false)} disabled={run.isPending || !report}>
              {promote ? 'Promote students' : 'Move students'}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
