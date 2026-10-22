import { useQuery } from '@tanstack/react-query';
import {
  ArrowRight,
  Banknote,
  BellRing,
  CheckCircle2,
  ChartColumn,
  CreditCard,
  Landmark,
  MessageCircle,
  ReceiptText,
  ShieldCheck,
  Smartphone,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import handsUp from '../assets/photos/hands-up.webp';
import pupil from '../assets/photos/pupil-smiling.webp';
import pupilsReading from '../assets/photos/pupils-reading.webp';
import teacher from '../assets/photos/teacher-classroom.webp';
import { BRAND } from '../lib/brand';

const FACTS = [
  ['1', 'account number for every child'],
  ['Seconds', 'from a transfer to a receipt'],
  ['₦0', 'for parents, always'],
  ['3 terms', 'with arrears carried forward'],
] as const;

const AUDIENCES: { icon: LucideIcon; who: string; title: string; text: string; link: string; cta: string }[] = [
  {
    icon: ChartColumn,
    who: 'Proprietors',
    title: 'Know where every naira is',
    text: 'Who has paid and who owes, by class.',
    link: '/signup',
    cta: 'Sign your school up',
  },
  {
    icon: Wallet,
    who: 'Bursars',
    title: 'No more matching narrations',
    text: 'Payments match and receipt themselves.',
    link: '#how',
    cta: 'See how it works',
  },
  {
    icon: Smartphone,
    who: 'Parents',
    title: 'Pay from your bank app',
    text: 'Each child has their own account number.',
    link: '/parent',
    cta: 'Pay school fees',
  },
];

const STEPS = [
  ['Load your school', 'Classes, pupils and this term’s fees.'],
  ['Send the bills', 'One click bills every pupil, by WhatsApp or text.'],
  ['Watch the money come in', 'Transfers are matched and receipted in seconds.'],
] as const;

const FEATURES: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Landmark, title: 'Automatic reconciliation', text: 'Each child’s account number pays their bill.' },
  { icon: ReceiptText, title: 'Receipts that can’t be faked', text: 'Numbered, with a QR code anyone can check.' },
  { icon: BellRing, title: 'Polite reminders', text: 'WhatsApp and text, never at night.' },
  { icon: Banknote, title: 'Cash and tellers too', text: 'Approved by a second person before they count.' },
  { icon: CreditCard, title: 'Instalments', text: 'Spread a bill over agreed dates.' },
  { icon: ShieldCheck, title: 'Your money, your account', text: 'Payments go straight to the school’s bank.' },
];

const FAQ: [string, string][] = [
  ['Do you hold our money?', 'No. Payments go straight into your school’s own bank account.'],
  ['Do we have to change banks?', 'No. Every child’s account number pays into the account you already use.'],
  ['What if a parent pays the wrong amount?', 'The oldest debt is cleared first; anything extra becomes credit for the next bill.'],
  ['How long does it take to set up?', 'An afternoon, with a step-by-step guide.'],
];

/** The public website: what WardPay does, how it's priced, and where to start. */
export function Landing() {
  const info = useQuery({ queryKey: ['public-info'], queryFn: async () => unwrap(await api.GET('/api/v1/public/info')) });
  const name = info.data?.app_name ?? BRAND;
  const signupOpen = info.data?.signup_open !== false;
  return (
    <div className="site">
      <header className="site-bar">
        <Link to="/" className="site-brand">
          {name}
        </Link>
        <nav aria-label="Site">
          <a href="#how" className="site-nav-extra">
            How it works
          </a>
          <a href="#pricing" className="site-nav-extra">
            Pricing
          </a>
          <Link to="/parent">Parents</Link>
          <Link to="/console/login">Sign in</Link>
          {signupOpen && (
            <Link to="/signup" className="button small">
              Sign your school up
            </Link>
          )}
        </nav>
      </header>
      <main>
        <section className="hero split">
          <div className="hero-copy">
            <span className="eyebrow">Built for Nigerian schools</span>
            <h1>School fees, collected and reconciled for you</h1>
            <p className="lead">Every child gets their own account number. Transfers match, receipt and remind on their own.</p>
            <div className="row wrap">
              {signupOpen && (
                <Link to="/signup" className="button">
                  Sign your school up <ArrowRight size={18} aria-hidden />
                </Link>
              )}
              <Link to="/parent" className="button secondary">
                I&apos;m a parent: pay school fees
              </Link>
            </div>
            <ul className="hero-ticks">
              <li>
                <CheckCircle2 size={18} aria-hidden /> Free for parents
              </li>
              <li>
                <CheckCircle2 size={18} aria-hidden /> Money goes straight to your school&apos;s bank
              </li>
            </ul>
          </div>
          <div className="hero-visual">
            <img src={pupil} alt="A smiling pupil in a light-blue school uniform at his desk, pen in hand" width={1400} height={1050} />
            <div className="float-card paid" aria-hidden>
              <span className="float-icon">
                <CheckCircle2 size={20} />
              </span>
              <div>
                <strong>₦85,000 received</strong>
                <span>Tolu A., JSS 2 · receipt sent</span>
              </div>
            </div>
            <div className="float-card reminder" aria-hidden>
              <span className="float-icon whatsapp">
                <MessageCircle size={20} />
              </span>
              <div>
                <strong>Reminder sent</strong>
                <span>Tuition ₦120,000 · due Friday</span>
              </div>
            </div>
          </div>
        </section>

        <section className="facts" aria-label="At a glance">
          {FACTS.map(([big, small]) => (
            <div key={small}>
              <strong>{big}</strong>
              <span>{small}</span>
            </div>
          ))}
        </section>

        <section className="site-section" aria-labelledby="who">
          <h2 id="who">One system for everyone who touches school fees</h2>
          <div className="audience-grid">
            {AUDIENCES.map(({ icon: Icon, who, title, text, link, cta }) => (
              <div key={who} className="card audience">
                <span className="feature-icon">
                  <Icon size={22} aria-hidden />
                </span>
                <span className="eyebrow">{who}</span>
                <h3>{title}</h3>
                <p>{text}</p>
                {link.startsWith('#') ? (
                  <a href={link} className="text-link">
                    {cta} <ArrowRight size={16} aria-hidden />
                  </a>
                ) : (
                  (link !== '/signup' || signupOpen) && (
                    <Link to={link} className="text-link">
                      {cta} <ArrowRight size={16} aria-hidden />
                    </Link>
                  )
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="site-section" aria-labelledby="how">
          <div className="split-block">
            <div>
              <h2 id="how">How it works</h2>
              <ol className="steps-list">
                {STEPS.map(([title, text], i) => (
                  <li key={title}>
                    <span className="step-number">{i + 1}</span>
                    <div>
                      <h3>{title}</h3>
                      <p>{text}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
            <div className="photo-with-card">
              <img
                src={teacher}
                alt="A teacher in a white shirt talking to a class of secondary-school pupils"
                width={1200}
                height={900}
                loading="lazy"
              />
              <div className="mock-receipt" aria-label="An example receipt">
                <div className="mock-receipt-head">
                  <span className="mock-logo" aria-hidden>
                    G
                  </span>
                  <div>
                    <strong>Greenfield College</strong>
                    <span>Receipt R-000231</span>
                  </div>
                  <span className="badge ok">Paid</span>
                </div>
                <dl>
                  <div>
                    <dt>Pupil</dt>
                    <dd>Tolu A., JSS 2</dd>
                  </div>
                  <div>
                    <dt>Tuition</dt>
                    <dd>₦70,000</dd>
                  </div>
                  <div>
                    <dt>PTA levy</dt>
                    <dd>₦15,000</dd>
                  </div>
                  <div className="total">
                    <dt>Received</dt>
                    <dd>₦85,000</dd>
                  </div>
                </dl>
                <p className="mock-note">
                  <ShieldCheck size={16} aria-hidden /> Anyone can check it with its QR code
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className="site-section" aria-labelledby="features">
          <h2 id="features">What your bursary gets</h2>
          <div className="feature-grid">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <div key={title} className="card">
                <span className="feature-icon">
                  <Icon size={22} aria-hidden />
                </span>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="site-section" aria-labelledby="parents">
          <div className="parent-band">
            <div className="mock-phone-wrap">
              <img
                src={pupilsReading}
                alt="Two pupils in checked uniforms working together at a classroom desk"
                width={1200}
                height={900}
                loading="lazy"
              />
              <div className="mock-phone" aria-label="An example of what a parent sees">
                <span className="mock-phone-label">Pay into Tolu&apos;s own account</span>
                <strong className="mock-account">9930 000 001</strong>
                <span className="mock-phone-label">Wema Bank · Greenfield College</span>
                <div className="mock-owed">
                  <span>Owed this term</span>
                  <strong>₦120,000</strong>
                  <small>Tuition ₦100,000 · PTA levy ₦20,000</small>
                </div>
                <span className="mock-phone-button">Pay by card instead</span>
              </div>
            </div>
            <div className="parent-band-copy">
              <span className="eyebrow">For parents</span>
              <h2 id="parents">No queues at the bank. No lost tellers.</h2>
              <ul className="tick-list">
                {['Pay from any bank app', 'All your children on one page', 'A receipt by text, straight away'].map((t) => (
                  <li key={t}>
                    <CheckCircle2 size={18} aria-hidden /> {t}
                  </li>
                ))}
              </ul>
              <Link to="/parent" className="button">
                Pay school fees
              </Link>
            </div>
          </div>
        </section>

        <section className="photo-banner" aria-labelledby="banner">
          <img src={handsUp} alt="A class of pupils with their hands up, eager to answer" width={1800} height={750} loading="lazy" />
          <h2 id="banner">More time for teaching. Less time chasing fees.</h2>
        </section>

        <section className="site-section" aria-labelledby="pricing">
          <h2 id="pricing">Pricing for schools</h2>
          <p className="muted section-note">What a school pays to use {name}.</p>
          <div className="card pricing">
            <ul className="price-list">
              <li>
                <strong>Set-up fee</strong> once, when you join
              </li>
              <li>
                <strong>Pupil fee</strong> per pupil, each term
              </li>
            </ul>
            <p className="muted small">Bank and card charges come off each payment.</p>
          </div>
        </section>

        <section className="site-section" aria-labelledby="faq">
          <h2 id="faq">Questions schools ask</h2>
          <div className="faq">
            {FAQ.map(([q, a]) => (
              <details key={q}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="site-section" aria-labelledby="privacy">
          <h2 id="privacy">Children&apos;s data, handled carefully</h2>
          <p>
            Encrypted, kept apart per school, and handled under the Nigeria Data Protection Act 2023.{' '}
            <Link to="/privacy">Read our privacy notice</Link>.
          </p>
        </section>

        {signupOpen && (
          <section className="site-section">
            <div className="cta-band">
              <h2>Start next term with the fees already in hand</h2>
              <Link to="/signup" className="button">
                Sign your school up <ArrowRight size={18} aria-hidden />
              </Link>
            </div>
          </section>
        )}
      </main>
      <footer className="site-footer">
        <span>
          © {new Date().getFullYear()} {name}
        </span>
        <Link to="/privacy">Privacy</Link>
        {info.data?.support_email && <a href={`mailto:${info.data.support_email}`}>{info.data.support_email}</a>}
        <small className="credits">
          Photos: Ben White (Unsplash), Mickael Ange Konan, Roman Odintsov and Emmanuel Abiodun (Pexels). Names, numbers and the school in
          the examples are made up.
        </small>
      </footer>
    </div>
  );
}
