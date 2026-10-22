import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { formatKobo, parseNairaToKobo } from '../lib/format';
import { ErrorAlert, Field, Loading, PageHeader } from '../lib/ui';
import { Generate } from './billing/Billing';
import { useClassLevels, useCurrentTerm, useSessions } from './hooks';
import { ClassSetup } from './setup/ClassSetup';
import { SessionForm } from './setup/Calendar';

/** The wizard's steps, in order; `key` matches the getting-started checklist (GET /school/onboarding). */
const STEPS = [
  { key: 'profile', title: 'School details' },
  { key: 'calendar', title: 'School year' },
  { key: 'classes', title: 'Classes' },
  { key: 'fees', title: 'Fees' },
  { key: 'students', title: 'Pupils' },
  { key: 'bills', title: 'Bills' },
] as const;
type StepKey = (typeof STEPS)[number]['key'];

/**
 * Set up a new school step by step: details, the school year (filled in), classes (ticked), this
 * term's fees (once per section), pupils, then the term's bills. Each step can be skipped and come
 * back to; progress comes from the same checklist the dashboard shows.
 */
export function GetStarted() {
  const queryClient = useQueryClient();
  const onboarding = useQuery({
    queryKey: ['onboarding'],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/school/onboarding')),
  });
  const [step, setStep] = useState<StepKey | null>(null);
  const done = new Set((onboarding.data ?? []).filter((s) => s.done).map((s) => s.key));
  // Open on the first step not done yet
  useEffect(() => {
    if (step === null && onboarding.data) setStep(STEPS.find((s) => !done.has(s.key))?.key ?? 'bills');
  }, [onboarding.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (onboarding.isPending || step === null) return <Loading />;
  if (onboarding.isError) return <ErrorAlert error={onboarding.error} />;
  const index = STEPS.findIndex((s) => s.key === step);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['onboarding'] });
  const next = () => {
    refresh();
    setStep(STEPS[Math.min(index + 1, STEPS.length - 1)]!.key);
  };

  return (
    <>
      <PageHeader title="Set up your school" subtitle="Six short steps. Skip any and come back later; your progress is kept." />
      <ol className="stepper" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s.key}>
            <button
              type="button"
              className={`step ${s.key === step ? 'current' : ''} ${done.has(s.key) ? 'done' : ''}`}
              aria-current={s.key === step ? 'step' : undefined}
              onClick={() => setStep(s.key)}
            >
              <span className="step-dot" aria-hidden="true">
                {done.has(s.key) ? <Check size={14} strokeWidth={3} /> : i + 1}
              </span>
              <span className="step-title">{s.title}</span>
            </button>
          </li>
        ))}
      </ol>
      <section className="card wizard-card" aria-label={STEPS[index]!.title}>
        <h2>
          {index + 1}. {STEPS[index]!.title}
        </h2>
        {step === 'profile' && <ProfileStep onDone={next} />}
        {step === 'calendar' && <CalendarStep onDone={next} />}
        {step === 'classes' && <ClassesStep onDone={next} />}
        {step === 'fees' && <FeesStep onDone={next} />}
        {step === 'students' && <PupilsStep done={done.has('students')} onDone={next} />}
        {step === 'bills' && <BillsStep done={done.has('bills')} hasPupils={done.has('students')} onChange={refresh} />}
        <div className="row between wizard-nav">
          <button type="button" className="secondary" disabled={index === 0} onClick={() => setStep(STEPS[index - 1]!.key)}>
            Back
          </button>
          {index < STEPS.length - 1 ? (
            <button type="button" className="link" onClick={next}>
              {done.has(step) ? 'Next' : 'Skip for now'}
            </button>
          ) : (
            <Link to="/console">Go to the dashboard</Link>
          )}
        </div>
      </section>
    </>
  );
}

function ProfileStep({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const school = useQuery({ queryKey: ['school'], queryFn: async () => unwrap(await api.GET('/api/v1/school')) });
  const [form, setForm] = useState<{ name: string; address: string; phone: string; email: string } | null>(null);
  useEffect(() => {
    if (school.data && form === null) {
      const { name, address, phone, email } = school.data;
      setForm({ name, address, phone, email });
    }
  }, [school.data, form]);
  const save = useMutation({
    mutationFn: async () => {
      // Empty optional fields are left as they are rather than sent as invalid values
      const body = Object.fromEntries(Object.entries(form ?? {}).filter(([k, v]) => k === 'address' || v !== ''));
      return unwrap(await api.PATCH('/api/v1/school', { body }));
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['school'], data);
      onDone();
    },
  });
  if (!form) return <Loading />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit}>
      <p className="muted">These appear on receipts and on the parents' pages.</p>
      <ErrorAlert error={save.error} />
      <Field label="School name">
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} />
      </Field>
      <Field label="Address">
        <textarea rows={2} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
      </Field>
      <div className="grid-2 tight">
        <Field label="Phone">
          <input inputMode="tel" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
        </Field>
        <Field label="Email">
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
      </div>
      <button type="submit" disabled={save.isPending}>
        Save and continue
      </button>
    </form>
  );
}

function CalendarStep({ onDone }: { onDone: () => void }) {
  const sessions = useSessions();
  if (sessions.isPending) return <Loading />;
  const current = (sessions.data ?? []).flatMap((s) => s.terms.map((t) => ({ session: s.name, term: t }))).find((x) => x.term.is_current);
  if ((sessions.data ?? []).length > 0) {
    return (
      <p>
        Done: {current ? `${current.session} ${current.term.label} is the current term.` : 'your school year is set up.'}{' '}
        <Link to="/console/calendar">See the calendar</Link>
      </p>
    );
  }
  return (
    <>
      <p className="muted">We've filled in this session with the usual term dates. Change any date, then create it.</p>
      <SessionForm hasCurrent={false} onDone={onDone} />
    </>
  );
}

function ClassesStep({ onDone }: { onDone: () => void }) {
  const levels = useClassLevels();
  if (levels.isPending) return <Loading />;
  const active = (levels.data ?? []).filter((l) => l.is_active);
  if (active.length > 0) {
    return (
      <p>
        Done: {active.length} classes, from {active[0]!.name} to {active[active.length - 1]!.name}.{' '}
        <Link to="/console/classes">Change them</Link>
      </p>
    );
  }
  return (
    <>
      <p className="muted">The usual classes are ticked. Untick any your school doesn't run and add your own.</p>
      <ClassSetup firstTime onDone={onDone} />
    </>
  );
}

const SECTION_ORDER = ['Preparatory', 'Primary', 'Junior secondary', 'Senior secondary', 'Other'];
type FeeRow = { name: string; category: Schemas['FeeCategory']; amounts: Record<string, string> };

/** This term's fees, typed once per section (Primary, JSS…) and set for every class in it. */
function FeesStep({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const term = useCurrentTerm();
  const levels = useClassLevels();
  const standard = useQuery({
    queryKey: ['class-levels', 'standard'],
    queryFn: async () => unwrap(await api.GET('/api/v1/class-levels/standard')),
  });
  const items = useQuery({ queryKey: ['fee-items'], queryFn: async () => unwrap(await api.GET('/api/v1/fee-items')) });
  const [rows, setRows] = useState<FeeRow[]>([
    { name: 'Tuition', category: 'tuition', amounts: {} },
    { name: 'Development levy', category: 'levy', amounts: {} },
    { name: 'PTA levy', category: 'levy', amounts: {} },
  ]);
  const save = useMutation({
    mutationFn: async () => {
      const known = new Map((items.data ?? []).map((i) => [i.name.toLowerCase(), i.id]));
      const entries: Schemas['ScheduleEntryIn'][] = [];
      for (const row of rows) {
        const name = row.name.trim();
        if (!name || !classes.some((c) => row.amounts[c.section])) continue;
        let id = known.get(name.toLowerCase());
        if (!id) {
          id = unwrap(await api.POST('/api/v1/fee-items', { body: { name, category: row.category } })).id;
          known.set(name.toLowerCase(), id);
        }
        for (const c of classes) {
          const kobo = row.amounts[c.section] ? parseNairaToKobo(row.amounts[c.section]!) : null;
          if (kobo) entries.push({ fee_item_id: id, class_level_id: c.id, amount_kobo: kobo, is_optional: false });
        }
      }
      if (entries.length === 0) throw new Error('Type at least one amount.');
      return unwrap(
        await api.PUT('/api/v1/terms/{term_id}/fee-schedule', { params: { path: { term_id: term.data!.id } }, body: { entries } }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fee-items'] });
      void queryClient.invalidateQueries({ queryKey: ['fee-schedule'] });
      onDone();
    },
  });

  if (term.isPending || levels.isPending || standard.isPending) return <Loading />;
  if (!term.data) return <p>Set up the school year first (step 2), so there's a term to set fees for.</p>;
  const sectionOf = new Map((standard.data ?? []).map((s) => [s.name.toLowerCase(), s.group]));
  const classes = (levels.data ?? [])
    .filter((l) => l.is_active)
    .map((l) => ({ id: l.id, name: l.name, section: sectionOf.get(l.name.toLowerCase()) ?? 'Other' }));
  if (classes.length === 0) return <p>Add your classes first (step 3).</p>;
  const sections = SECTION_ORDER.filter((s) => classes.some((c) => c.section === s));
  const bad = rows.some((r) => Object.values(r.amounts).some((a) => a && parseNairaToKobo(a) === null));
  const setAmount = (i: number, section: string, value: string) =>
    setRows(rows.map((r, n) => (n === i ? { ...r, amounts: { ...r.amounts, [section]: value } } : r)));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="muted">
        Type each fee once per section; every class in it gets that amount for this term. Leave a box empty if the section doesn't pay it.
        Fine-tune a single class, or mark a fee optional (like the bus), on the Fees page afterwards.
      </p>
      <ErrorAlert error={save.error} />
      <div className="table-scroll">
        <table className="grid quick-fees">
          <thead>
            <tr>
              <th>Fee</th>
              {sections.map((s) => (
                <th
                  key={s}
                  title={classes
                    .filter((c) => c.section === s)
                    .map((c) => c.name)
                    .join(', ')}
                >
                  {s} (₦)
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td>
                  <input
                    aria-label={`Fee ${i + 1} name`}
                    value={row.name}
                    onChange={(e) => setRows(rows.map((r, n) => (n === i ? { ...r, name: e.target.value } : r)))}
                    placeholder="e.g. Exam fee"
                  />
                </td>
                {sections.map((s) => (
                  <td key={s}>
                    <input
                      aria-label={`${row.name || `Fee ${i + 1}`} for ${s}`}
                      inputMode="decimal"
                      value={row.amounts[s] ?? ''}
                      onChange={(e) => setAmount(i, s, e.target.value)}
                      className={row.amounts[s] && parseNairaToKobo(row.amounts[s]!) === null ? 'invalid' : ''}
                      placeholder="0"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row wrap section">
        <button type="button" className="secondary small" onClick={() => setRows([...rows, { name: '', category: 'other', amounts: {} }])}>
          Add another fee
        </button>
        <span className="hint">
          Fees already set this term are kept. See them all on the <Link to="/console/fees">Fees page</Link>.
        </span>
      </div>
      <button type="submit" disabled={bad || save.isPending}>
        Save fees and continue
      </button>
    </form>
  );
}

function PupilsStep({ done, onDone }: { done: boolean; onDone: () => void }) {
  return (
    <>
      <p>
        {done
          ? 'Done: you have pupils on the roll. Add more any time.'
          : "Upload the school's existing list (Excel or CSV) with each child's class and parent: we check every row before saving."}
      </p>
      <div className="row wrap">
        <Link to="/console/students/import" className="button">
          Import from a spreadsheet
        </Link>
        <Link to="/console/students" className="button secondary">
          Add pupils one by one
        </Link>
        {done && (
          <button type="button" className="link" onClick={onDone}>
            On to bills
          </button>
        )}
      </div>
    </>
  );
}

function BillsStep({ done, hasPupils, onChange }: { done: boolean; hasPupils: boolean; onChange: () => void }) {
  const term = useCurrentTerm();
  const [open, setOpen] = useState(false);
  const summary = useQuery({
    queryKey: ['billing-summary', term.data?.id],
    enabled: !!term.data,
    queryFn: async () => unwrap(await api.GET('/api/v1/terms/{term_id}/billing-summary', { params: { path: { term_id: term.data!.id } } })),
  });
  if (term.isPending) return <Loading />;
  if (!term.data) return <p>Set up the school year first (step 2).</p>;
  if (!hasPupils) return <p>Add your pupils first (step 5): bills go to every pupil in a class.</p>;
  return (
    <>
      {done && summary.data ? (
        <p>
          Done: {summary.data.invoices} bills for this term, {formatKobo(summary.data.billed_kobo)} in all.{' '}
          <Link to="/console/billing">See them</Link>
        </p>
      ) : (
        <p>Every pupil in a class gets their bill for the term in one go: their class's fees, extras they take, and their discounts.</p>
      )}
      <button onClick={() => setOpen(true)}>{done ? 'Bill pupils added since' : "Generate this term's bills"}</button>
      {open && (
        <Generate
          termId={term.data.id}
          onClose={() => {
            setOpen(false);
            onChange();
          }}
        />
      )}
    </>
  );
}
