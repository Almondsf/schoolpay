import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { BRAND } from '../../lib/brand';
import { ErrorAlert, Field, Notice, PageHeader } from '../../lib/ui';

type Report = Schemas['ImportReportOut'];
type Mode = 'skip' | 'update';

/** Upload a spreadsheet, preview what would happen (with every problem by row), then import. */
export function ImportStudents() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>('skip');
  const [consent, setConsent] = useState(false);
  const [preview, setPreview] = useState<Report | null>(null);
  const [result, setResult] = useState<Report | null>(null);

  const upload = useMutation({
    mutationFn: async (dryRun: boolean) =>
      unwrap(
        await api.POST('/api/v1/students/import', {
          body: { file: '', dry_run: dryRun, mode, consent_confirmed: consent },
          bodySerializer: (body) => {
            const form = new FormData();
            form.append('file', file!);
            form.append('dry_run', String(body.dry_run));
            form.append('mode', String(body.mode));
            form.append('consent_confirmed', String(body.consent_confirmed));
            return form;
          },
        }),
      ),
    onSuccess: (report) => {
      if (report.dry_run) setPreview(report);
      else {
        setResult(report);
        void queryClient.invalidateQueries({ queryKey: ['students'] });
      }
    },
  });

  const download = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.GET('/api/v1/students/import-template', { parseAs: 'blob' });
      if (!response.ok || !data) throw new Error('Could not download the template.');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(data as Blob);
      link.download = 'schoolpay-students-template.xlsx';
      link.click();
      URL.revokeObjectURL(link.href);
    },
  });

  function choose(event: FormEvent<HTMLInputElement>) {
    setFile(event.currentTarget.files?.[0] ?? null);
    setPreview(null);
    setResult(null);
  }

  if (result) {
    return (
      <>
        <PageHeader title="Import finished" />
        <div className="card narrow">
          <Notice>
            {result.students_created} students added, {result.students_updated} updated, {result.students_skipped} already there.{' '}
            {result.guardians_created} parents added.
          </Notice>
          <Problems report={result} />
          <div className="row">
            <Link className="button" to="/console/students">
              See students
            </Link>
            <button className="secondary" onClick={() => setResult(null)}>
              Import another file
            </button>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <p>
        <Link to="/console/students">← Students</Link>
      </p>
      <PageHeader title="Import students" subtitle="Load your student list from Excel (.xlsx) or CSV. You'll see a preview before anything is saved." />
      <div className="grid-2">
        <div className="card">
          <h2>1. Prepare the file</h2>
          <p>
            Use your own spreadsheet or our template. You need columns for admission number, surname, first name and class. Parent name and
            phone are optional but needed for receipts and reminders.
          </p>
          <button className="secondary" onClick={() => download.mutate()} disabled={download.isPending}>
            Download template
          </button>
          <ErrorAlert error={download.error} />
        </div>
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            upload.mutate(true);
          }}
        >
          <h2>2. Upload and check</h2>
          <Field label="Spreadsheet">
            <input type="file" accept=".xlsx,.csv" onChange={choose} />
          </Field>
          <Field label={`Students already in ${BRAND} (same admission number)`}>
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="skip">Leave them as they are</option>
              <option value="update">Update their names, class and parents from the file</option>
            </select>
          </Field>
          <label className="checkbox">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            Parents agreed to the school storing their contact details for fees and school messages.
          </label>
          <ErrorAlert error={upload.error} />
          <button type="submit" disabled={!file || upload.isPending}>
            {upload.isPending ? 'Checking…' : 'Check file'}
          </button>
        </form>
      </div>

      {preview && (
        <div className="card section">
          <h2>3. Review</h2>
          <p>
            <strong>{preview.total_rows}</strong> rows: {preview.students_created} new students, {preview.students_updated} to update,{' '}
            {preview.students_skipped} already there, {preview.guardians_created} new parents.
            {preview.errors.length > 0 && (
              <>
                {' '}
                <strong className="error-text">{preview.errors.length} problems</strong> (those rows won't be imported).
              </>
            )}
          </p>
          <Problems report={preview} />
          {preview.preview.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Admission no.</th>
                  <th>Name</th>
                  <th>Class</th>
                  <th>Parents</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {preview.preview.map((p) => (
                  <tr key={p.row}>
                    <td>{p.row}</td>
                    <td>{p.admission_number}</td>
                    <td>{p.name}</td>
                    <td>{p.class_name}</td>
                    <td>{p.guardians}</td>
                    <td>{{ create: 'Add', update: 'Update', skip: 'Skip' }[p.action] ?? p.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row end">
            <button onClick={() => upload.mutate(false)} disabled={upload.isPending || !consent && preview.guardians_created > 0}>
              Import {preview.students_created + preview.students_updated} students
            </button>
          </div>
          {!consent && preview.guardians_created > 0 && <p className="hint">Tick the consent box above to import parents' details.</p>}
        </div>
      )}
    </>
  );
}

function Problems({ report }: { report: Report }) {
  if (report.errors.length === 0) return null;
  return (
    <details open={report.errors.length <= 20}>
      <summary>
        {report.errors.length} row{report.errors.length === 1 ? '' : 's'} with problems
      </summary>
      <ul className="problems">
        {report.errors.map((e, i) => (
          <li key={i}>
            Row {e.row}: {e.message}
          </li>
        ))}
      </ul>
    </details>
  );
}
