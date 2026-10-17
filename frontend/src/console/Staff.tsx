import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime, ROLE_LABELS } from '../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, StatusBadge, fieldError } from '../lib/ui';

type Staff = Schemas['StaffOut'];
type StaffRole = Schemas['StaffInviteIn']['role'];
const ROLES: StaffRole[] = ['proprietor', 'bursar', 'school_admin'];
const PAGE = 50;

export function StaffPage() {
  const { can, user } = useAuth();
  const manage = can('staff.manage');
  const [offset, setOffset] = useState(0);
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Staff | null>(null);
  const staff = useQuery({
    queryKey: ['staff', offset],
    queryFn: async () => unwrap(await api.GET('/api/v1/staff', { params: { query: { limit: PAGE, offset } } })),
  });

  return (
    <>
      <PageHeader
        title="Staff"
        subtitle="People who can sign in to your school's console."
        actions={manage && <button onClick={() => setInviting(true)}>Invite staff</button>}
      />
      {staff.isPending ? (
        <Loading />
      ) : staff.isError ? (
        <ErrorAlert error={staff.error} />
      ) : staff.data.items.length === 0 ? (
        <Empty>No staff yet.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Status</th>
                <th>Two-factor</th>
                <th>Last sign-in</th>
                {manage && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {staff.data.items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <strong>{s.full_name}</strong>
                    <div className="muted small">{s.email}</div>
                  </td>
                  <td>{ROLE_LABELS[s.role]}</td>
                  <td>
                    <StatusBadge status={!s.is_active ? 'inactive' : s.invite_pending ? 'invited' : 'active'} />
                  </td>
                  <td>{s.mfa_enabled ? 'On' : 'Off'}</td>
                  <td>{formatDateTime(s.last_login_at)}</td>
                  {manage && (
                    <td className="actions">
                      {s.id !== user?.id && (
                        <button className="secondary small" onClick={() => setEditing(s)}>
                          Manage
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={staff.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {inviting && <InviteModal onClose={() => setInviting(false)} />}
      {editing && <ManageModal staff={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function InviteModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ email: '', full_name: '', role: 'bursar' as StaffRole });
  const invite = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/staff', { body: form })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['staff'] });
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    invite.mutate();
  }

  return (
    <Modal title="Invite staff" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="muted">They'll get an email with a link to choose a password.</p>
        <ErrorAlert error={fieldError(invite.error, 'email') ? null : invite.error} />
        <Field label="Full name">
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required minLength={2} />
        </Field>
        <Field label="Email" error={fieldError(invite.error, 'email')}>
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        </Field>
        <Field label="Role" hint={ROLE_HINTS[form.role]}>
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as StaffRole })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={invite.isPending}>
            Send invite
          </button>
        </div>
      </form>
    </Modal>
  );
}

const ROLE_HINTS: Record<StaffRole, string> = {
  proprietor: 'Full control, including staff. Must use two-factor sign-in.',
  bursar: 'Fees, payments and reminders. Must use two-factor sign-in.',
  school_admin: 'Classes and students. Can view fees and record payments.',
};

function ManageModal({ staff, onClose }: { staff: Staff; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [role, setRole] = useState<StaffRole>(staff.role as StaffRole);
  const [fullName, setFullName] = useState(staff.full_name);
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['staff'] });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () =>
      unwrap(await api.PATCH('/api/v1/staff/{user_id}', { params: { path: { user_id: staff.id } }, body: { role, full_name: fullName } })),
    onSuccess: done,
  });
  const action = useMutation({
    mutationFn: async (kind: 'deactivate' | 'reactivate' | 'reset-mfa' | 'resend-invite') => {
      const path = { params: { path: { user_id: staff.id } } };
      const calls = {
        deactivate: () => api.POST('/api/v1/staff/{user_id}/deactivate', path),
        reactivate: () => api.POST('/api/v1/staff/{user_id}/reactivate', path),
        'reset-mfa': () => api.POST('/api/v1/staff/{user_id}/reset-mfa', path),
        'resend-invite': () => api.POST('/api/v1/staff/{user_id}/resend-invite', path),
      };
      return unwrap(await calls[kind]());
    },
    onSuccess: done,
  });

  return (
    <Modal title={`Manage ${staff.full_name}`} onClose={onClose}>
      <ErrorAlert error={save.error ?? action.error} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Full name">
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} required minLength={2} />
        </Field>
        <Field label="Role" hint="Changing the role signs them out so the new permissions apply.">
          <select value={role} onChange={(e) => setRole(e.target.value as StaffRole)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </Field>
        <div className="row end">
          <button type="submit" disabled={save.isPending}>
            Save
          </button>
        </div>
      </form>
      <hr />
      <div className="row wrap">
        {staff.invite_pending && staff.is_active && (
          <button className="secondary" onClick={() => action.mutate('resend-invite')}>
            Resend invite
          </button>
        )}
        {staff.mfa_enabled && (
          <button
            className="secondary"
            onClick={() => window.confirm('Turn off their two-factor sign-in so they can set it up on a new phone?') && action.mutate('reset-mfa')}
          >
            Reset two-factor
          </button>
        )}
        {staff.is_active ? (
          <button
            className="danger"
            onClick={() => window.confirm(`Deactivate ${staff.full_name}? They'll be signed out straight away.`) && action.mutate('deactivate')}
          >
            Deactivate
          </button>
        ) : (
          <button className="secondary" onClick={() => action.mutate('reactivate')}>
            Reactivate
          </button>
        )}
      </div>
    </Modal>
  );
}
