import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, humanize } from '../../lib/format';
import { ErrorAlert, Field, Loading, Modal, PageHeader, StatusBadge, fieldError } from '../../lib/ui';
import { StudentAccount } from '../billing/StudentAccount';
import { StudentPayIn } from '../payments/StudentPayIn';
import { armOptions, useClassLevels } from '../hooks';

type Detail = Schemas['StudentDetailOut'];
type Guardian = Schemas['GuardianOut'];

export function StudentDetail() {
  const { studentId = '' } = useParams();
  const { can } = useAuth();
  const manage = can('students.manage');
  const queryClient = useQueryClient();
  const student = useQuery({
    queryKey: ['student', studentId],
    queryFn: async () => unwrap(await api.GET('/api/v1/students/{student_id}', { params: { path: { student_id: studentId } } })),
  });
  const [dialog, setDialog] = useState<'edit' | 'class' | 'withdraw' | 'guardian' | null>(null);
  const [editingGuardian, setEditingGuardian] = useState<Guardian | null>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['student', studentId] });
    void queryClient.invalidateQueries({ queryKey: ['students'] });
  };
  const path = { params: { path: { student_id: studentId } } };
  const reinstate = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/students/{student_id}/reinstate', path)),
    onSuccess: refresh,
  });
  const guardianAction = useMutation({
    mutationFn: async ({ kind, guardianId }: { kind: 'primary' | 'remove'; guardianId: string }) => {
      const p = { params: { path: { student_id: studentId, guardian_id: guardianId } } };
      if (kind === 'primary') return unwrap(await api.POST('/api/v1/students/{student_id}/guardians/{guardian_id}/primary', p));
      return unwrap(await api.DELETE('/api/v1/students/{student_id}/guardians/{guardian_id}', p));
    },
    onSuccess: refresh,
  });

  if (student.isPending) return <Loading />;
  if (student.isError) return <ErrorAlert error={student.error} />;
  const s = student.data;
  const current = s.enrollments.find((e) => e.is_current);

  return (
    <>
      <p>
        <Link to="/console/students">← Students</Link>
      </p>
      <PageHeader
        title={s.full_name}
        subtitle={
          <>
            {s.admission_number} · {current ? `${current.placement.class_level_name} ${current.placement.arm_name}` : 'Not placed this term'} ·{' '}
            <StatusBadge status={s.status} />
          </>
        }
        actions={
          manage && (
            <>
              <button className="secondary" onClick={() => setDialog('edit')}>
                Edit details
              </button>
              {s.status === 'active' ? (
                <>
                  <button className="secondary" onClick={() => setDialog('class')}>
                    Change class
                  </button>
                  <button className="secondary" onClick={() => setDialog('withdraw')}>
                    Withdraw
                  </button>
                </>
              ) : (
                <button className="secondary" onClick={() => reinstate.mutate()}>
                  Reinstate
                </button>
              )}
            </>
          )
        }
      />
      <ErrorAlert error={reinstate.error ?? guardianAction.error} />
      <div className="grid-2">
        <div className="card">
          <h2>Details</h2>
          <dl className="details">
            <dt>Gender</dt>
            <dd>{s.gender ? humanize(s.gender) : '—'}</dd>
            <dt>Date of birth</dt>
            <dd>{formatDate(s.date_of_birth)}</dd>
            {s.left_on && (
              <>
                <dt>Left on</dt>
                <dd>{formatDate(s.left_on)}</dd>
              </>
            )}
          </dl>
          <h2>Class history</h2>
          {s.enrollments.length === 0 ? (
            <p className="muted">Not placed in any term yet.</p>
          ) : (
            <ul className="plain">
              {s.enrollments.map((e) => (
                <li key={e.term_id}>
                  {e.session_name} {e.term_label}: <strong>{`${e.placement.class_level_name} ${e.placement.arm_name}`}</strong>
                  {e.is_current && <span className="badge ok">Current</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <div className="row between">
            <h2>Parents and guardians</h2>
            {manage && (
              <button className="secondary small" onClick={() => setDialog('guardian')}>
                Add
              </button>
            )}
          </div>
          {s.guardians.length === 0 && <p className="muted">No guardian yet. Add one so they get receipts and reminders.</p>}
          {s.guardians.map((g) => (
            <div className="guardian" key={g.id}>
              <div>
                <strong>{g.full_name}</strong> <span className="muted">({humanize(g.relationship)})</span>{' '}
                {g.is_primary && <span className="badge ok">Gets fee notices</span>}
                <div className="small">
                  {g.phone}
                  {g.email && ` · ${g.email}`}
                </div>
              </div>
              {manage && (
                <div className="row wrap">
                  <button className="link small" onClick={() => setEditingGuardian(g)}>
                    Edit
                  </button>
                  {!g.is_primary && (
                    <button className="link small" onClick={() => guardianAction.mutate({ kind: 'primary', guardianId: g.id })}>
                      Make main contact
                    </button>
                  )}
                  <button
                    className="link small"
                    onClick={() => window.confirm(`Unlink ${g.full_name} from ${s.first_name}?`) && guardianAction.mutate({ kind: 'remove', guardianId: g.id })}
                  >
                    Unlink
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      {can('fees.view') && <StudentAccount studentId={s.id} />}
      {can('payments.view') && <StudentPayIn studentId={s.id} />}
      {dialog === 'edit' && <EditStudent student={s} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === 'class' && <ChangeClass student={s} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === 'withdraw' && <Withdraw student={s} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === 'guardian' && <AddGuardian student={s} onClose={() => setDialog(null)} onDone={refresh} />}
      {editingGuardian && <EditGuardian guardian={editingGuardian} onClose={() => setEditingGuardian(null)} onDone={refresh} />}
    </>
  );
}

interface DialogProps {
  student: Detail;
  onClose: () => void;
  onDone: () => void;
}

function EditStudent({ student, onClose, onDone }: DialogProps) {
  const [form, setForm] = useState({
    admission_number: student.admission_number,
    first_name: student.first_name,
    middle_name: student.middle_name,
    last_name: student.last_name,
    gender: (student.gender ?? '') as '' | 'male' | 'female',
    date_of_birth: student.date_of_birth ?? '',
  });
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH('/api/v1/students/{student_id}', {
          params: { path: { student_id: student.id } },
          body: { ...form, gender: form.gender || null, date_of_birth: form.date_of_birth || null },
        }),
      ),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return (
    <Modal title="Edit details" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <ErrorAlert error={save.error} />
        <div className="grid-2 tight">
          <Field label="Admission number" error={fieldError(save.error, 'admission_number')}>
            <input value={form.admission_number} onChange={set('admission_number')} required />
          </Field>
          <Field label="First name">
            <input value={form.first_name} onChange={set('first_name')} required />
          </Field>
          <Field label="Middle name">
            <input value={form.middle_name} onChange={set('middle_name')} />
          </Field>
          <Field label="Surname">
            <input value={form.last_name} onChange={set('last_name')} required />
          </Field>
          <Field label="Gender">
            <select value={form.gender} onChange={set('gender')}>
              <option value="">—</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </Field>
          <Field label="Date of birth">
            <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} />
          </Field>
        </div>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={save.isPending}>
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ChangeClass({ student, onClose, onDone }: DialogProps) {
  const levels = useClassLevels();
  const current = student.enrollments.find((e) => e.is_current);
  const [armId, setArmId] = useState(current?.placement.arm_id ?? '');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.PUT('/api/v1/students/{student_id}/class', { params: { path: { student_id: student.id } }, body: { arm_id: armId } })),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title="Change class for this term" onClose={onClose}>
      <ErrorAlert error={save.error} />
      <Field label="Class">
        <select value={armId} onChange={(e) => setArmId(e.target.value)}>
          <option value="">Choose…</option>
          {armOptions(levels.data).map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          Cancel
        </button>
        <button onClick={() => save.mutate()} disabled={!armId || save.isPending}>
          Save
        </button>
      </div>
    </Modal>
  );
}

function Withdraw({ student, onClose, onDone }: DialogProps) {
  const [leftOn, setLeftOn] = useState(new Date().toISOString().slice(0, 10));
  const [reason, setReason] = useState('');
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/students/{student_id}/withdraw', {
          params: { path: { student_id: student.id } },
          body: { left_on: leftOn, reason },
        }),
      ),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title={`Withdraw ${student.first_name}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>Their records and any unpaid balance are kept. They won't be billed for new terms.</p>
        <ErrorAlert error={save.error} />
        <Field label="Date they left">
          <input type="date" value={leftOn} onChange={(e) => setLeftOn(e.target.value)} required />
        </Field>
        <Field label="Reason">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="danger" disabled={save.isPending}>
            Withdraw
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AddGuardian({ student, onClose, onDone }: DialogProps) {
  const [form, setForm] = useState({ full_name: '', phone: '', email: '', relationship: 'guardian' as Schemas['Relationship'] });
  const [consent, setConsent] = useState(false);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/students/{student_id}/guardians', {
          params: { path: { student_id: student.id } },
          body: { ...form, email: form.email || null, consent_confirmed: consent },
        }),
      ),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title="Add parent or guardian" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted">If this phone number already belongs to a parent at the school (e.g. a sibling's), they're linked instead of duplicated.</p>
        <ErrorAlert error={save.error} />
        <Field label="Name">
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required minLength={2} />
        </Field>
        <Field label="Phone" error={fieldError(save.error, 'phone')}>
          <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} required inputMode="tel" />
        </Field>
        <Field label="Relationship">
          <select value={form.relationship} onChange={(e) => setForm({ ...form, relationship: e.target.value as Schemas['Relationship'] })}>
            <option value="mother">Mother</option>
            <option value="father">Father</option>
            <option value="guardian">Guardian</option>
            <option value="other">Other</option>
          </select>
        </Field>
        <Field label="Email (optional)">
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <label className="checkbox">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
          They agreed to the school storing these contact details for fees and school messages.
        </label>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={save.isPending}>
            Add
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditGuardian({ guardian, onClose, onDone }: { guardian: Guardian; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({ full_name: guardian.full_name, phone: guardian.phone, email: guardian.email ?? '' });
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH('/api/v1/guardians/{guardian_id}', {
          params: { path: { guardian_id: guardian.id } },
          body: { full_name: form.full_name, phone: form.phone, email: form.email || null, clear_email: !form.email },
        }),
      ),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title={`Edit ${guardian.full_name}`} onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted">Changes apply to all of their children at this school. Their phone number is also how they sign in to the parent portal.</p>
        <ErrorAlert error={save.error} />
        <Field label="Name">
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
        </Field>
        <Field label="Phone" error={fieldError(save.error, 'phone')}>
          <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} required inputMode="tel" />
        </Field>
        <Field label="Email">
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={save.isPending}>
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
