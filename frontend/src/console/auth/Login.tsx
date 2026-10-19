import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../../auth/AuthContext';
import { BRAND } from '../../lib/brand';
import { ErrorAlert, Field } from '../../lib/ui';

export function Login() {
  const { user, login, completeMfa } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const next = (location.state as { from?: string } | null)?.from ?? '/console';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [ticket, setTicket] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={next} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (ticket) {
        await completeMfa(ticket, code.trim(), useRecovery);
      } else {
        const mfaTicket = await login(email, password);
        if (mfaTicket) {
          setTicket(mfaTicket);
          return;
        }
      }
      navigate(next, { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>{ticket ? 'Two-factor sign-in' : `Sign in to ${BRAND}`}</h1>
        <ErrorAlert error={error} />
        {ticket ? (
          <>
            <p className="muted">
              {useRecovery ? 'Enter one of your recovery codes.' : 'Enter the 6-digit code from your authenticator app.'}
            </p>
            <Field label={useRecovery ? 'Recovery code' : 'Code'}>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode={useRecovery ? 'text' : 'numeric'}
                autoComplete="one-time-code"
                autoFocus
                required
              />
            </Field>
            <button type="submit" disabled={busy}>
              {busy ? 'Checking…' : 'Continue'}
            </button>
            <button type="button" className="link" onClick={() => { setUseRecovery(!useRecovery); setCode(''); }}>
              {useRecovery ? 'Use my authenticator app instead' : 'Lost your phone? Use a recovery code'}
            </button>
          </>
        ) : (
          <>
            <Field label="Email">
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" autoFocus required />
            </Field>
            <Field label="Password">
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </Field>
            <button type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
            <Link to="/console/forgot-password" className="link">
              Forgot your password?
            </Link>
            <Link to="/signup" className="link">
              New school? Sign up
            </Link>
          </>
        )}
      </form>
    </main>
  );
}
