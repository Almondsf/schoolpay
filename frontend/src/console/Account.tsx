import QRCode from 'qrcode';
import { useEffect, useState, type FormEvent } from 'react';

import { api, unwrap } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ROLE_LABELS } from '../lib/format';
import { ErrorAlert, Field, Notice, PageHeader, fieldError } from '../lib/ui';

export function Account() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <>
      <PageHeader title="Your account" subtitle={`${user.full_name} · ${user.email} · ${ROLE_LABELS[user.role] ?? user.role}`} />
      {user.mfa_enrolment_required && (
        <Notice>Your role handles money and staff, so two-factor sign-in is required. Set it up below to continue.</Notice>
      )}
      <div className="grid-2">
        <TwoFactor />
        {!user.mfa_enrolment_required && <ChangePassword />}
      </div>
    </>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setDone(false);
    try {
      unwrap(await api.POST('/api/v1/auth/password/change', { body: { current_password: current, new_password: next } }));
      setDone(true);
      setCurrent('');
      setNext('');
    } catch (e) {
      setError(e);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Password</h2>
      {done && <Notice>Password changed. Other devices have been signed out.</Notice>}
      <ErrorAlert error={fieldError(error, 'current_password') || fieldError(error, 'new_password') ? null : error} />
      <Field label="Current password" error={fieldError(error, 'current_password')}>
        <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
      </Field>
      <Field label="New password" hint="At least 10 characters." error={fieldError(error, 'new_password')}>
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
      </Field>
      <button type="submit">Change password</button>
    </form>
  );
}

function TwoFactor() {
  const { user, refreshUser } = useAuth();
  const [password, setPassword] = useState('');
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState('');
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (setup) QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 200 }).then(setQr, () => setQr(''));
  }, [setup]);

  async function start(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setSetup(unwrap(await api.POST('/api/v1/auth/mfa/setup', { body: { password } })));
      setPassword('');
    } catch (e) {
      setError(e);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = unwrap(await api.POST('/api/v1/auth/mfa/confirm', { body: { totp_code: code.trim() } }));
      setCodes(result.recovery_codes);
      setSetup(null);
      setCode('');
    } catch (e) {
      setError(e);
    }
  }

  async function finish() {
    setCodes(null);
    await refreshUser();
  }

  if (codes) {
    return (
      <div className="card">
        <h2>Save your recovery codes</h2>
        <p>
          If you lose your phone, each of these codes lets you sign in once. Store them somewhere safe. They won't be
          shown again.
        </p>
        <ul className="codes">
          {codes.map((c) => (
            <li key={c}>
              <code>{c}</code>
            </li>
          ))}
        </ul>
        <button type="button" onClick={finish}>
          I've saved them
        </button>
      </div>
    );
  }

  if (user?.mfa_enabled) {
    return (
      <div className="card">
        <h2>Two-factor sign-in</h2>
        <p>
          <span className="badge ok">On</span> You sign in with your password and a code from your authenticator app.
        </p>
        <p className="muted">Lost your phone? Sign in with a recovery code, or ask your proprietor to reset it.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Two-factor sign-in</h2>
      <ErrorAlert error={error} />
      {setup ? (
        <form onSubmit={confirm}>
          <ol className="steps">
            <li>Open Google Authenticator, Microsoft Authenticator or Authy on your phone.</li>
            <li>Scan this code, or type the key.</li>
          </ol>
          {qr && <img src={qr} alt="QR code for your authenticator app" width={200} height={200} />}
          <p>
            Key: <code className="secret">{setup.secret}</code>
          </p>
          <Field label="Code from the app" error={fieldError(error, 'totp_code')}>
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" required />
          </Field>
          <button type="submit">Turn on</button>
        </form>
      ) : (
        <form onSubmit={start}>
          <p>Protect your account with a code from your phone each time you sign in.</p>
          <Field label="Confirm your password" error={fieldError(error, 'password')}>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </Field>
          <button type="submit">Set up</button>
        </form>
      )}
    </div>
  );
}
