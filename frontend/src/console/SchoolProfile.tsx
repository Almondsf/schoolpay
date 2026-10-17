import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';

import { api, unwrap } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ErrorAlert, Field, Loading, Notice, PageHeader, fieldError } from '../lib/ui';

const FIELDS = ['name', 'address', 'phone', 'email'];

export function SchoolProfile() {
  const { can } = useAuth();
  const editable = can('school.manage');
  const queryClient = useQueryClient();
  const school = useQuery({ queryKey: ['school'], queryFn: async () => unwrap(await api.GET('/api/v1/school')) });
  const [form, setForm] = useState({ name: '', address: '', phone: '', email: '' });

  useEffect(() => {
    if (school.data) {
      const { name, address, phone, email } = school.data;
      setForm({ name, address, phone, email });
    }
  }, [school.data]);

  const save = useMutation({
    mutationFn: async () => {
      // Empty optional fields are left unchanged rather than sent as invalid values
      const body = Object.fromEntries(Object.entries(form).filter(([k, v]) => k === 'address' || v !== ''));
      return unwrap(await api.PATCH('/api/v1/school', { body }));
    },
    onSuccess: (data) => queryClient.setQueryData(['school'], data),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  if (school.isPending) return <Loading />;
  if (school.isError) return <ErrorAlert error={school.error} />;

  return (
    <>
      <PageHeader title="School profile" subtitle={`Short name: ${school.data.slug}`} />
      <form className="card narrow" onSubmit={submit}>
        {save.isSuccess && <Notice>Saved.</Notice>}
        <ErrorAlert error={FIELDS.some((f) => fieldError(save.error, f)) ? null : save.error} />
        <fieldset disabled={!editable}>
          <Field label="School name" error={fieldError(save.error, 'name')}>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} />
          </Field>
          <Field label="Address" error={fieldError(save.error, 'address')}>
            <textarea value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} rows={2} />
          </Field>
          <Field label="Phone" error={fieldError(save.error, 'phone')}>
            <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} inputMode="tel" />
          </Field>
          <Field label="Email" error={fieldError(save.error, 'email')}>
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          {editable && (
            <button type="submit" disabled={save.isPending}>
              Save
            </button>
          )}
        </fieldset>
      </form>
    </>
  );
}
