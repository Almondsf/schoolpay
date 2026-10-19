import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import { BRAND } from '../lib/brand';

/**
 * Plain-language privacy notice for parents and schools (NDPA 2023). It describes
 * what the system actually does (docs/PRIVACY.md); keep the two in step.
 */
export function PrivacyNotice() {
  const info = useQuery({ queryKey: ['public-info'], queryFn: async () => unwrap(await api.GET('/api/v1/public/info')) });
  const name = info.data?.app_name ?? BRAND;
  const contact = info.data?.support_email;
  return (
    <div className="site">
      <header className="site-bar">
        <Link to="/" className="site-brand">
          {name}
        </Link>
      </header>
      <main className="site-section prose">
        <h1>Privacy notice</h1>
        <p>
          {name} helps schools bill fees and collect payments. Your child&apos;s school decides what to collect and why: it is the{' '}
          <strong>data controller</strong>. {name} processes the data on the school&apos;s behalf as its <strong>data processor</strong>, under
          the Nigeria Data Protection Act 2023.
        </p>

        <h2>What we hold</h2>
        <ul>
          <li>About pupils: name, admission number, class each term, and (only if the school adds them) gender and date of birth.</li>
          <li>About parents: name, relationship to the child, phone number and email (both encrypted), and when consent was given.</li>
          <li>Bills, payments and receipts, and the messages sent to you about them.</li>
        </ul>
        <p>We don&apos;t collect BVNs, NINs, photos, addresses, religion or health information. Card details never reach us.</p>

        <h2>Why</h2>
        <p>
          To bill school fees, match and receipt payments, send you bills, receipts and reminders, and let you see your children&apos;s
          accounts. Nothing else: no advertising, and we never sell data.
        </p>

        <h2>Who it&apos;s shared with</h2>
        <ul>
          <li>The payment gateway (Paystack or Flutterwave), to give each child a bank account number and take card payments.</li>
          <li>Meta (WhatsApp), Termii (text messages) and an email provider, to deliver messages to you.</li>
        </ul>
        <p>Each is bound by its own data protection duties. Your child&apos;s school sees only its own pupils and parents.</p>

        <h2>How long we keep it</h2>
        <p>
          Schools must keep financial records for six years. When a pupil has been gone longer than that, their details (and yours, if you
          have no other child at the school) are erased automatically; the amounts stay, anonymised. The text of messages is deleted after
          a year. Sign-in codes are deleted within a day.
        </p>

        <h2>Your rights</h2>
        <ul>
          <li>
            <strong>Get a copy</strong>: in the parent portal, use &ldquo;Download my data&rdquo; for everything held about you and your
            children.
          </li>
          <li>
            <strong>Stop reminders</strong>: turn bill messages and reminders off in the portal (receipts still come).
          </li>
          <li>
            <strong>Correction or erasure</strong>: ask your child&apos;s school. Once your child has left, the school can erase your details;
            receipts already issued are kept as financial records.
          </li>
        </ul>

        <h2>Contact</h2>
        <p>
          For anything about your data, contact your child&apos;s school first.
          {contact && (
            <>
              {' '}
              You can also write to us at <a href={`mailto:${contact}`}>{contact}</a>.
            </>
          )}{' '}
          You may complain to the Nigeria Data Protection Commission.
        </p>
        <p>
          <Link to="/">← Back</Link>
        </p>
      </main>
    </div>
  );
}
