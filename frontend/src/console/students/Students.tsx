import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, StatusBadge, fieldError } from '../../lib/ui';
import { armOptions, useClassLevels, useCurrentTerm, useSessions } from '../hooks';
import { PrintPayCards } from '../payments/PayCards';

const PAGE = 50;
type Status = NonNullable<Schemas['StudentListItem']['status']>;

/** Narrower views of the roll; the school overview links here with ?show=… (and ?class=…). */
const SHOW_LABELS = {
  unplaced: 'Not placed this term',
  parent: 'No parent recorded',
  consent: 'No parent consent',
  account_number: 'No account number',
} as const;
type Show = keyof typeof SHOW_LABELS;
const isShow = (v: string | null): v is Show => !!v && v in SHOW_LABELS;

export function Students() {
  const { can } = useAuth();
  const manage = can('students.manage');
  const sessions = useSessions();
  const current = useCurrentTerm();
  const levels = useClassLevels();
  const [params] = useSearchParams();
  const [termId, setTermId] = useState('');
  const [search, setSearch] = useState('');
  const [levelId, setLevelId] = useState(params.get('class') ?? '');
  const [show, setShow] = useState<Show | ''>(isShow(params.get('show')) ? (params.get('show') as Show) : '');
  const [status, setStatus] = useState<Status>('active');
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const [printing, setPrinting] = useState(false);

  const effectiveTerm = termId || current.data?.id || '';
  const students = useQuery({
    queryKey: ['students', effectiveTerm, search, levelId, status, show, offset],
    enabled: !!effectiveTerm,
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/students', {
          params: {
            query: {
              term_id: effectiveTerm,
              search: search || undefined,
              class_level_id: levelId || undefined,
              status,
              unplaced: show === 'unplaced' || undefined,
              missing: show && show !== 'unplaced' ? show : undefined,
              limit: PAGE,
              offset,
            },
          },
        }),
      ),
  });
  const reset = <T,>(setter: (v: T) => void) => (value: T) => {
    setter(value);
    setOffset(0);
  };

  if (current.isPending) return <Loading />;
  if (!current.data) {
    return (
      <>
        <PageHeader title="Students" />
        <Empty>
          Set up the <Link to="/console/calendar">school calendar</Link> and <Link to="/console/classes">classes</Link> first.
        </Empty>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Students"
        subtitle={students.data ? `${students.data.total} ${status} student${students.data.total === 1 ? '' : 's'}` : undefined}
        actions={
          <>
            {can('payments.record') && (
              <button className="secondary" onClick={() => setPrinting(true)}>
                Print pay cards
              </button>
            )}
            {manage && (
              <>
                <Link className="button secondary" to="/console/students/import">
                  Import from spreadsheet
                </Link>
                <button onClick={() => setAdding(true)}>Add student</button>
              </>
            )}
          </>
        }
      />
      <div className="filters">
        <input type="search" placeholder="Search name or admission number" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        <select aria-label="Term" value={effectiveTerm} onChange={(e) => reset(setTermId)(e.target.value)}>
          {(sessions.data ?? []).flatMap((s) =>
            s.terms.map((t) => (
              <option key={t.id} value={t.id}>
                {s.name} · {t.label}
                {t.is_current ? ' (current)' : ''}
              </option>
            )),
          )}
        </select>
        <select aria-label="Class" value={levelId} onChange={(e) => reset(setLevelId)(e.target.value)}>
          <option value="">All classes</option>
          {(levels.data ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
        <select aria-label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value as Status)}>
          <option value="active">Current students</option>
          <option value="withdrawn">Withdrawn</option>
          <option value="graduated">Graduated</option>
        </select>
        <select aria-label="Show" value={show} onChange={(e) => reset(setShow)(e.target.value as Show | '')}>
          <option value="">Everyone</option>
          {Object.entries(SHOW_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {students.isPending ? (
        <Loading />
      ) : students.isError ? (
        <ErrorAlert error={students.error} />
      ) : students.data.items.length === 0 ? (
        <Empty>No students match.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Admission no.</th>
                <th>Class</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {students.data.items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/console/students/${s.id}`}>{s.full_name}</Link>
                  </td>
                  <td>{s.admission_number}</td>
                  <td>{s.placement ? `${s.placement.class_level_name} ${s.placement.arm_name}` : <span className="muted">Not placed</span>}</td>
                  <td>
                    <StatusBadge status={s.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={students.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {adding && <AddStudent onClose={() => setAdding(false)} />}
      {printing && <PrintPayCards classId={levelId} onClose={() => setPrinting(false)} />}
    </>
  );
}

function AddStudent({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const levels = useClassLevels();
  const options = armOptions(levels.data);
  const [form, setForm] = useState({
    admission_number: '',
    first_name: '',
    middle_name: '',
    last_name: '',
    gender: '' as '' | 'male' | 'female',
    date_of_birth: '',
    arm_id: '',
  });
  const [guardian, setGuardian] = useState({ full_name: '', phone: '', email: '', relationship: 'mother' as Schemas['Relationship'] });
  const [consent, setConsent] = useState(false);
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/students', {
          body: {
            ...form,
            gender: form.gender || null,
            date_of_birth: form.date_of_birth || null,
            guardians: guardian.phone ? [{ ...guardian, email: guardian.email || null }] : [],
            consent_confirmed: consent,
          },
        }),
      ),
    onSuccess: (student) => {
      void queryClient.invalidateQueries({ queryKey: ['students'] });
      navigate(`/console/students/${student.id}`);
    },
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Modal title="Add student" onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorAlert error={create.error} />
        <div className="grid-2 tight">
          <Field label="Admission number" error={fieldError(create.error, 'admission_number')}>
            <input value={form.admission_number} onChange={set('admission_number')} required maxLength={30} />
          </Field>
          <Field label="Class">
            <select value={form.arm_id} onChange={set('arm_id')} required>
              <option value="">Choose…</option>
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="First name">
            <input value={form.first_name} onChange={set('first_name')} required maxLength={60} />
          </Field>
          <Field label="Surname">
            <input value={form.last_name} onChange={set('last_name')} required maxLength={60} />
          </Field>
          <Field label="Middle name (optional)">
            <input value={form.middle_name} onChange={set('middle_name')} maxLength={60} />
          </Field>
          <Field label="Gender (optional)">
            <select value={form.gender} onChange={set('gender')}>
              <option value="">—</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </Field>
        </div>
        <h3>Parent or guardian</h3>
        <div className="grid-2 tight">
          <Field label="Name">
            <input value={guardian.full_name} onChange={(e) => setGuardian({ ...guardian, full_name: e.target.value })} />
          </Field>
          <Field label="Phone" hint="Used for receipts, reminders and the parent portal" error={fieldError(create.error, 'phone')}>
            <input value={guardian.phone} onChange={(e) => setGuardian({ ...guardian, phone: e.target.value })} inputMode="tel" />
          </Field>
          <Field label="Relationship">
            <select value={guardian.relationship} onChange={(e) => setGuardian({ ...guardian, relationship: e.target.value as Schemas['Relationship'] })}>
              <option value="mother">Mother</option>
              <option value="father">Father</option>
              <option value="guardian">Guardian</option>
              <option value="other">Other</option>
            </select>
          </Field>
          <Field label="Email (optional)">
            <input type="email" value={guardian.email} onChange={(e) => setGuardian({ ...guardian, email: e.target.value })} />
          </Field>
        </div>
        {guardian.phone && (
          <label className="checkbox">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
            The parent agreed to the school storing these contact details for fees and school messages.
          </label>
        )}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={create.isPending}>
            Add student
          </button>
        </div>
      </form>
    </Modal>
  );
}
