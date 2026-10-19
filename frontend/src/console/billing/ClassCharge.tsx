import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { formatKobo, parseNairaToKobo } from '../../lib/format';
import { ErrorAlert, Field, Modal, fieldError } from '../../lib/ui';
import { useClassLevels } from '../hooks';

type Report = Schemas['ClassChargeOut'];

/**
 * Add a fee to whole classes for the term (WAEC for SSS 3, an end-of-year party). It goes on bills
 * already issued and on bills generated later; untick pupils who aren't taking part.
 */
export function ClassCharge({ termId, onClose }: { termId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const levels = useClassLevels();
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  const [itemId, setItemId] = useState('');
  const [newName, setNewName] = useState('');
  const [amount, setAmount] = useState('');
  const [classIds, setClassIds] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const kobo = parseNairaToKobo(amount);

  const run = useMutation({
    mutationFn: async (dryRun: boolean) =>
      unwrap(
        await api.POST('/api/v1/terms/{term_id}/class-charges', {
          params: { path: { term_id: termId } },
          body: {
            fee_item_id: itemId || null,
            new_item_name: itemId ? null : newName,
            amount_kobo: kobo ?? 0,
            class_level_ids: classIds,
            excluded_student_ids: [...excluded],
            dry_run: dryRun,
          },
        }),
      ),
    onSuccess: (report) => {
      if (!report.dry_run) {
        for (const key of ['invoices', 'billing-summary', 'account', 'fee-items', 'fee-schedule', 'reports-overview', 'debtors']) {
          void queryClient.invalidateQueries({ queryKey: [key] });
        }
      }
    },
  });
  const report: Report | undefined = run.data;
  const finished = report && !report.dry_run;
  const ready = (itemId || newName.trim().length >= 2) && kobo && kobo > 0 && classIds.length > 0;
  const changed = () => run.reset(); // any change to the details needs a new preview

  const toggleClass = (id: string) => {
    setClassIds(classIds.includes(id) ? classIds.filter((c) => c !== id) : [...classIds, id]);
    setExcluded(new Set());
    changed();
  };
  const toggleStudent = (id: string) => {
    const next = new Set(excluded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExcluded(next);
  };
  const chargedCount = report ? report.students.filter((s) => !excluded.has(s.student_id)).length : 0;

  return (
    <Modal title="Add a fee to classes" onClose={onClose}>
      {finished ? (
        <>
          <p>
            <strong>{report.item_name}</strong> ({formatKobo(report.amount_kobo)}) added. {report.charged_now} bills were updated now; pupils
            not billed yet will get it when the term is billed.
          </p>
          <div className="row end">
            <button onClick={onClose}>Close</button>
          </div>
        </>
      ) : (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            run.mutate(!report);
          }}
        >
          <p className="muted small">
            For fees that only some classes pay, or that come up during the term: WAEC, NECO, JAMB, an end-of-year party. It goes on bills
            already sent and on bills generated later.
          </p>
          <div className="grid-2 tight">
            <Field label="Fee">
              <select
                value={itemId}
                onChange={(e) => {
                  setItemId(e.target.value);
                  changed();
                }}
              >
                <option value="">A new fee…</option>
                {(items.data ?? [])
                  .filter((i) => i.is_active)
                  .map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name}
                    </option>
                  ))}
              </select>
            </Field>
            {!itemId && (
              <Field label="New fee's name" error={fieldError(run.error, 'new_item_name')}>
                <input
                  value={newName}
                  onChange={(e) => {
                    setNewName(e.target.value);
                    changed();
                  }}
                  placeholder="e.g. WAEC registration"
                  maxLength={80}
                />
              </Field>
            )}
          </div>
          <Field label="Amount per pupil (₦)" error={amount && kobo === null ? 'Not a valid amount' : undefined}>
            <input
              value={amount}
              inputMode="decimal"
              onChange={(e) => {
                setAmount(e.target.value);
                changed();
              }}
            />
          </Field>
          <fieldset className="class-picker">
            <legend className="field-label">Classes</legend>
            {(levels.data ?? [])
              .filter((l) => l.is_active)
              .map((l) => (
                <label key={l.id} className="checkbox">
                  <input type="checkbox" checked={classIds.includes(l.id)} onChange={() => toggleClass(l.id)} />
                  {l.name}
                </label>
              ))}
          </fieldset>
          <ErrorAlert error={run.error && !fieldError(run.error, 'new_item_name') ? run.error : null} />
          {report && (
            <div className="card inset">
              <strong>
                {chargedCount} of {report.students.length} pupils will pay {report.item_name}: {formatKobo(chargedCount * report.amount_kobo)}
              </strong>
              <p className="muted small">Untick anyone not taking part. They're remembered, so billing later won't charge them.</p>
              <div className="student-ticks">
                {report.students.map((s) => (
                  <label key={s.student_id} className="checkbox">
                    <input type="checkbox" checked={!excluded.has(s.student_id)} onChange={() => toggleStudent(s.student_id)} />
                    <span>
                      {s.full_name} <span className="muted small">{s.class_name}</span>
                      {!s.billed && <span className="muted small"> · not billed yet</span>}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="row end">
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            {report ? (
              <button type="submit" disabled={run.isPending}>
                Add to {chargedCount} pupils
              </button>
            ) : (
              <button type="submit" disabled={!ready || run.isPending}>
                Preview
              </button>
            )}
          </div>
        </form>
      )}
    </Modal>
  );
}
