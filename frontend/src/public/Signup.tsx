import { useMutation } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import { BRAND } from '../lib/brand';
import { ErrorAlert, Field, fieldError } from '../lib/ui';

/** A school signs itself up. The proprietor confirms by email, which opens the school's account. */
export function Signup() {
  const [form, setForm] = useState({
    school_name: '',
    proprietor_name: '',
    email: '',
    phone: '',
    address: '',
    accept_terms: false,
    website: '', // honeypot: hidden from people, filled in by bots
  });
  const submit = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/public/signup', { body: form })),
  });
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  if (submit.isSuccess) {
    return (
      <main className="auth-page">
        <div className="card auth-card">
          <h1>Check your email</h1>
          <p>
            We've sent a link to <strong>{form.email}</strong>. Open it and choose a password: that confirms your email and opens your
            school's account.
          </p>
          <p className="muted small">Nothing there? Check your spam folder. The link works for a limited time.</p>
          <Link to="/console/login" className="link">
            Back to sign in
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <form
        className="card auth-card"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          submit.mutate();
        }}
      >
        <h1>Sign your school up</h1>
        <p className="muted">
          Fee invoicing, automatic matching of bank transfers, receipts and reminders. Free for parents. The school pays a set-up fee, a
          yearly licence and a small fee per pupil each term.
        </p>
        <ErrorAlert error={submit.error && !['email', 'phone', 'school_name'].some((f) => fieldError(submit.error, f)) ? submit.error : null} />
        <Field label="School name" error={fieldError(submit.error, 'school_name')}>
          <input value={form.school_name} onChange={set('school_name')} required minLength={3} maxLength={200} autoFocus />
        </Field>
        <Field label="Your name (proprietor)">
          <input value={form.proprietor_name} onChange={set('proprietor_name')} required minLength={2} maxLength={150} autoComplete="name" />
        </Field>
        <Field label="Your email" hint="We'll send a link here to confirm it." error={fieldError(submit.error, 'email')}>
          <input type="email" value={form.email} onChange={set('email')} required autoComplete="email" />
        </Field>
        <Field label="Phone" error={fieldError(submit.error, 'phone')}>
          <input type="tel" value={form.phone} onChange={set('phone')} required autoComplete="tel" placeholder="0803 123 4567" />
        </Field>
        <Field label="School address (optional)">
          <input value={form.address} onChange={set('address')} maxLength={300} autoComplete="street-address" />
        </Field>
        <div className="honeypot" aria-hidden="true">
          <label>
            Website
            <input tabIndex={-1} autoComplete="off" value={form.website} onChange={set('website')} />
          </label>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={form.accept_terms} onChange={set('accept_terms')} required />
          I accept the terms of service and agree to handle pupils' and parents' data under the NDPA.
        </label>
        <button type="submit" disabled={submit.isPending}>
          {submit.isPending ? 'Sending…' : 'Sign up'}
        </button>
        <Link to="/console/login" className="link">
          Already using {BRAND}? Sign in
        </Link>
      </form>
    </main>
  );
}
