import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import { BRAND } from '../lib/brand';
import { formatKobo } from '../lib/format';

const STEPS = [
  ['Load your school', 'Upload your existing student spreadsheet with each child’s parent, set your classes and this term’s fees.'],
  ['Send the bills', 'One click bills every pupil for the term, with sibling discounts and arrears carried forward. Parents get the bill by WhatsApp or text.'],
  ['Watch the money come in', 'Every child has their own bank account number. Transfers into it are matched and receipted automatically, within seconds.'],
] as const;

const FEATURES = [
  ['Automatic reconciliation', 'No more matching bank narrations by hand. A transfer into a child’s account number is that child’s payment.'],
  ['Numbered, verifiable receipts', 'Every payment gets a receipt with a QR code anyone can check, so fake receipts are caught.'],
  ['Polite reminders', 'WhatsApp and text reminders before and after the due date, stopping the moment a bill is paid. Never at night.'],
  ['A portal for parents', 'Parents sign in with their phone number and see all their children, what’s owed, and every receipt.'],
  ['Cash and tellers too', 'Record cash, teller and POS payments, approved by a second person before they count.'],
  ['The proprietor’s view', 'Billed, collected and outstanding by class, who owes and for how long, downloadable for Excel.'],
] as const;

/** The public website: what WardPay does, how it's priced, and where to start. */
export function Landing() {
  const info = useQuery({ queryKey: ['public-info'], queryFn: async () => unwrap(await api.GET('/api/v1/public/info')) });
  const name = info.data?.app_name ?? BRAND;
  return (
    <div className="site">
      <header className="site-bar">
        <span className="site-brand">{name}</span>
        <nav aria-label="Site">
          <Link to="/parent">Parents</Link>
          <Link to="/console/login">Sign in</Link>
          {info.data?.signup_open !== false && (
            <Link to="/signup" className="button small">
              Sign your school up
            </Link>
          )}
        </nav>
      </header>
      <main>
        <section className="hero">
          <h1>School fees, collected and reconciled for you</h1>
          <p className="lead">
            Bills for every pupil in one click, a bank account number for each child so transfers match themselves, receipts that can&apos;t
            be faked, and reminders that go out on their own. Built for Nigerian schools.
          </p>
          <div className="row wrap">
            <Link to="/signup" className="button">
              Sign your school up
            </Link>
            <Link to="/parent" className="button secondary">
              I&apos;m a parent: pay school fees
            </Link>
          </div>
        </section>

        <section className="site-section" aria-labelledby="how">
          <h2 id="how">How it works</h2>
          <ol className="steps-grid">
            {STEPS.map(([title, text], i) => (
              <li key={title} className="card">
                <span className="step-number">{i + 1}</span>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="site-section" aria-labelledby="features">
          <h2 id="features">What your bursary gets</h2>
          <div className="feature-grid">
            {FEATURES.map(([title, text]) => (
              <div key={title} className="card">
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="site-section" aria-labelledby="pricing">
          <h2 id="pricing">Pricing</h2>
          <div className="card pricing">
            <p className="lead">
              <strong>Free for parents.</strong> The school pays:
            </p>
            {info.data ? (
              <ul className="plain">
                <li>
                  <strong>{formatKobo(info.data.setup_fee_kobo)}</strong> set-up, once
                </li>
                <li>
                  <strong>{formatKobo(info.data.annual_fee_kobo)}</strong> licence per school year
                </li>
                <li>
                  <strong>{formatKobo(info.data.per_pupil_term_kobo)}</strong> per pupil, per term
                </li>
              </ul>
            ) : (
              <p>A one-off set-up fee, a yearly licence and a small fee per pupil each term.</p>
            )}
            <p className="muted small">
              Parents&apos; payments go straight into the school&apos;s own bank account; {name} never holds school money. Bank and card
              charges come off each payment.
            </p>
          </div>
        </section>

        <section className="site-section" aria-labelledby="privacy">
          <h2 id="privacy">Children&apos;s data, handled carefully</h2>
          <p>
            We collect only what fee collection needs, encrypt parents&apos; phone numbers and emails, keep every school&apos;s data apart,
            and follow the Nigeria Data Protection Act 2023. Parents can download their data at any time.{' '}
            <Link to="/privacy">Read our privacy notice</Link>.
          </p>
        </section>
      </main>
      <footer className="site-footer">
        <span>
          © {new Date().getFullYear()} {name}
        </span>
        <Link to="/privacy">Privacy</Link>
        {info.data?.support_email && <a href={`mailto:${info.data.support_email}`}>{info.data.support_email}</a>}
      </footer>
    </div>
  );
}
