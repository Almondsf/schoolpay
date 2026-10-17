import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { ErrorAlert, Field, Notice, fieldError } from '../../lib/ui';

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      unwrap(await api.POST('/api/v1/auth/password/forgot', { body: { email } }));
      setSent(true);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Reset your password</h1>
        {sent ? (
          <Notice>If that email has an account, we've sent a link to reset the password. Check your inbox.</Notice>
        ) : (
          <>
            <ErrorAlert error={error} />
            <Field label="Email">
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
            </Field>
            <button type="submit">Send reset link</button>
          </>
        )}
        <Link to="/console/login" className="link">
          Back to sign in
        </Link>
      </form>
    </main>
  );
}

/** Reached from invite and reset emails. The token is in the URL fragment, so it never reaches server logs. */
export function SetPassword() {
  const token = useMemo(() => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '', []);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(new Error("The two passwords don't match."));
      return;
    }
    try {
      unwrap(await api.POST('/api/v1/auth/password/set', { body: { token, new_password: password } }));
      window.history.replaceState(null, '', window.location.pathname); // drop the used token from the address bar
      setDone(true);
    } catch (e) {
      setError(e);
    }
  }

  if (!token) {
    return (
      <main className="auth-page">
        <div className="card auth-card">
          <h1>Link incomplete</h1>
          <p>Open the link from your email again, or ask for a new one.</p>
          <Link to="/console/forgot-password">Ask for a new link</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Choose a password</h1>
        {done ? (
          <>
            <Notice>Your password is set.</Notice>
            <Link to="/console/login">Sign in</Link>
          </>
        ) : (
          <>
            <ErrorAlert error={fieldError(error, 'new_password') ? null : error} />
            <Field label="New password" hint="At least 10 characters. A short sentence works well." error={fieldError(error, 'new_password')}>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={10} required />
            </Field>
            <Field label="Type it again">
              <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
            </Field>
            <button type="submit">Set password</button>
          </>
        )}
      </form>
    </main>
  );
}
