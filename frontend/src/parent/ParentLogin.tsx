import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../api/client';
import { ErrorAlert, Field, Notice } from '../lib/ui';
import { useParentAuth } from './ParentAuth';

type Step = 'phone' | 'code' | 'school';

/** Phone number, then the texted code, then (for families at several schools) which school. */
export function ParentLogin() {
  const { signedIn, acceptToken } = useParentAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [choice, setChoice] = useState<Schemas['ParentChooseSchool'] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (signedIn) return <Navigate to="/parent" replace />;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const finish = (token: string) => {
    acceptToken(token);
    navigate('/parent', { replace: true });
  };

  const sendCode = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      unwrap(await api.POST('/api/v1/parent/auth/request-code', { body: { phone } }));
      setStep('code');
    });
  };

  const checkCode = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const result = unwrap(await api.POST('/api/v1/parent/auth/verify', { body: { phone, code: code.trim() } }));
      if (result.status === 'signed_in') finish(result.access_token);
      else {
        setChoice(result);
        setStep('school');
      }
    });
  };

  const pickSchool = (guardianId: string) =>
    void run(async () => {
      const result = unwrap(await api.POST('/api/v1/parent/auth/choose-school', { body: { ticket: choice!.ticket, guardian_id: guardianId } }));
      finish(result.access_token);
    });

  return (
    <main className="parent-shell auth-page">
      <div className="card auth-card">
        <h1>School fees</h1>
        <ErrorAlert error={error} />
        {step === 'phone' && (
          <form onSubmit={sendCode}>
            <p className="muted">Sign in with the phone number your child's school has for you.</p>
            <Field label="Phone number">
              <input type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="0803 123 4567" required />
            </Field>
            <button type="submit" className="block" disabled={busy}>
              {busy ? 'Sending…' : 'Text me a code'}
            </button>
          </form>
        )}
        {step === 'code' && (
          <form onSubmit={checkCode}>
            <Notice>If {phone} is registered with a school, we've texted it a 6-digit code.</Notice>
            <Field label="Code">
              <input inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} autoFocus required />
            </Field>
            {import.meta.env.DEV && <DemoCode phone={phone} onCode={setCode} />}
            <button type="submit" className="block" disabled={busy}>
              {busy ? 'Checking…' : 'Sign in'}
            </button>
            <button type="button" className="link" onClick={() => { setStep('phone'); setCode(''); }}>
              Use a different number, or send a new code
            </button>
          </form>
        )}
        {step === 'school' && choice && (
          <div>
            <p>Your children are at more than one school. Which one?</p>
            {choice.schools.map((s) => (
              <button key={s.guardian_id} className="block secondary choice" onClick={() => pickSchool(s.guardian_id)} disabled={busy}>
                <span className="choice-school">{s.school_name}</span>
                {s.children.length > 0 && <span className="small muted"> {s.children.join(', ')}</span>}
              </button>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}

/**
 * Demos on a laptop: fill in the code the app "texted", read from the development inbox. Only in the
 * Vite dev server (`import.meta.env.DEV`); production builds drop it, and the inbox doesn't exist there.
 */
function DemoCode({ phone, onCode }: { phone: string; onCode: (code: string) => void }) {
  const [note, setNote] = useState('');
  async function fill() {
    setNote('');
    const response = await fetch(`/api/v1/dev/sms/latest?to=${encodeURIComponent(phone)}`);
    const text = response.ok ? ((await response.json()) as { text: string }).text : '';
    const code = /\b(\d{6})\b/.exec(text)?.[1];
    if (code) onCode(code);
    else setNote('No code was sent to this number (is it a parent at a school?).');
  }
  return (
    <p className="small">
      <button type="button" className="link" onClick={() => void fill()}>
        Fill in the code (demo on this computer)
      </button>
      {note && <span className="muted"> {note}</span>}
    </p>
  );
}
