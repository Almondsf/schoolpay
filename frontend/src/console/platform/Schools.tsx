import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { formatDate, formatKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Pager, StatusBadge, fieldError } from '../../lib/ui';

type School = Schemas['PlatformSchoolOut'];
type Status = Schemas['SchoolStatus'];
const PAGE = 50;

export function PlatformSchools() {
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const status = (params.get('status') as Status | null) ?? '';
  const [creating, setCreating] = useState(false);
  const [changing, setChanging] = useState<Pick<School, 'id' | 'name' | 'status'> | null>(null);
  const schools = useQuery({
    queryKey: ['platform-schools', search, status, offset],
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/platform/schools', {
          params: { query: { limit: PAGE, offset, search: search || undefined, status: status || undefined } },
        }),
      ),
  });

  return (
    <>
      <PageHeader title="Schools" actions={<button onClick={() => setCreating(true)}>New school</button>} />
      <div className="filters">
        <input
          type="search"
          placeholder="Name, short name or email"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => {
            setParams(e.target.value ? { status: e.target.value } : {});
            setOffset(0);
          }}
        >
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="pending">Waiting to confirm</option>
          <option value="suspended">Suspended</option>
        </select>
      </div>
      {schools.isPending ? (
        <Loading />
      ) : schools.isError ? (
        <ErrorAlert error={schools.error} />
      ) : schools.data.items.length === 0 ? (
        <Empty>No schools found.</Empty>
      ) : (
        <div className="card table-card">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>School</th>
                  <th>Status</th>
                  <th>Plan</th>
                  <th className="num">Pupils</th>
                  <th>Payments</th>
                  <th className="num">Owes us</th>
                  <th>Joined</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {schools.data.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/console/platform/schools/${s.id}`}>
                        <strong>{s.name}</strong>
                      </Link>
                      <div className="muted small">
                        {s.slug}
                        {s.proprietor_email && ` · ${s.proprietor_email}`}
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={s.status} />
                      {s.origin === 'signup' && <div className="muted small">Signed up</div>}
                    </td>
                    <td className="small">{s.plan_name}</td>
                    <td className="num">{s.pupils}</td>
                    <td className="small">{s.payments_connected ? 'Bank connected' : <span className="muted">Not set up</span>}</td>
                    <td className="num">{s.subscription_owed_kobo ? formatKobo(s.subscription_owed_kobo) : <span className="muted">—</span>}</td>
                    <td className="nowrap small">{formatDate(s.created_at)}</td>
                    <td className="actions">
                      {s.status !== 'pending' && (
                        <button className="secondary small" onClick={() => setChanging(s)}>
                          {s.status === 'active' ? 'Suspend' : 'Reactivate'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={schools.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
      {creating && <CreateSchool onClose={() => setCreating(false)} />}
      {changing && <ChangeStatus school={changing} onClose={() => setChanging(null)} />}
    </>
  );
}

function CreateSchool({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: '', slug: '', proprietor_name: '', proprietor_email: '' });
  const create = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/platform/schools', { body: form })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['platform-schools'] });
      onClose();
    },
  });
  const slugFromName = (name: string) =>
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60);

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Modal title="New school" onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorAlert error={fieldError(create.error, 'slug') || fieldError(create.error, 'proprietor_email') ? null : create.error} />
        <Field label="School name">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value, slug: slugFromName(e.target.value) })}
            required
            minLength={2}
          />
        </Field>
        <Field label="Short name" hint="Lowercase letters, numbers and hyphens." error={fieldError(create.error, 'slug')}>
          <input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} required pattern="[a-z0-9]+(-[a-z0-9]+)*" />
        </Field>
        <Field label="Proprietor's name">
          <input value={form.proprietor_name} onChange={(e) => setForm({ ...form, proprietor_name: e.target.value })} required minLength={2} />
        </Field>
        <Field label="Proprietor's email" hint="They'll get an invite to set a password." error={fieldError(create.error, 'proprietor_email')}>
          <input type="email" value={form.proprietor_email} onChange={(e) => setForm({ ...form, proprietor_email: e.target.value })} required />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={create.isPending}>
            Create school
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ChangeStatus({ school, onClose }: { school: Pick<School, 'id' | 'name' | 'status'>; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const suspend = school.status === 'active';
  const change = useMutation({
    mutationFn: async () => {
      const options = { params: { path: { school_id: school.id } }, body: { reason } };
      return unwrap(
        suspend
          ? await api.POST('/api/v1/platform/schools/{school_id}/suspend', options)
          : await api.POST('/api/v1/platform/schools/{school_id}/reactivate', options),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-schools'] });
      void queryClient.invalidateQueries({ queryKey: ['platform-school', school.id] });
      onClose();
    },
  });

  return (
    <Modal title={`${suspend ? 'Suspend' : 'Reactivate'} ${school.name}`} onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate();
        }}
      >
        {suspend && <p>Staff will be signed out and can't sign in until the school is reactivated. No data is deleted.</p>}
        <ErrorAlert error={change.error} />
        <Field label="Reason (kept in the activity log)">
          <input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={suspend ? 'danger' : ''} disabled={change.isPending}>
            {suspend ? 'Suspend' : 'Reactivate'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
