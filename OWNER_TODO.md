# Owner to-do list

Things only you (Oluwafemi) can do: decisions, accounts, paperwork and people.
The build carries on without them, but each blocks something real. Kept up to
date as the build goes on; newest additions are marked _(new)_.

Last updated: 2026-10-19 (product name and providers noted)

## Decisions

- [ ] _(new)_ **Set the actual prices.** The model is decided (ParentPay's:
      set-up fee + annual licence + per pupil per year, billed termly, free
      for parents; PLAN.md §3.4). The built-in "Standard" plan has
      placeholder amounts; change them in the platform console (Plans)
      before selling.
- [ ] **Product name: "ParentPay" for now** (decided 2026-10-19; it's one setting,
      `APP_NAME`). _(new)_ **Check the trademark before a public launch:** ParentPay
      is the brand of ParentPay Ltd (UK), the product we modelled this on. Using it
      publicly in Nigeria could draw a complaint, and they may expand into Africa.

## Accounts to open

| Account | What to do | Blocks |
|---|---|---|
| **Paystack** (business) | Open the account, get test keys, then ask Paystack what approval/KYC **Dedicated Virtual Accounts** need for a platform with subaccounts (PLAN.md §3.2). Send me the answer. | Per-student account numbers on real money |
| **Flutterwave** | Optional second gateway. Permanent virtual accounts need each customer's BVN, so we use it for card payments only unless they offer another route. | Card payments via Flutterwave |
| **Meta WhatsApp Business** | _(new)_ You plan to use **Zoho** for WhatsApp: Zoho Voice can send WhatsApp messages (approved templates) for you, but **not WhatsApp calls**, and Meta reportedly doesn't allow business-started WhatsApp calls in Nigeria. Either way: create a WhatsApp Business account and phone number in Meta Business Manager, a permanent access token, and submit the three message templates in `docs/DEPLOYMENT.md` ("Messages to parents") for approval. _(new)_ The bill and reminder templates' last parameter is now the parent's family payment link. | WhatsApp receipts and reminders (SMS is used until then) |
| **Termii** (SMS) | Open an account, register a sender ID (up to 11 characters, e.g. the product name), and enable the DND route. | Parent sign-in codes and SMS messages on a live system |
| **Email sending** | _(new)_ **Zoho Mail chosen** (2026-10-19). Create the mailbox on your domain, then set `EMAIL_BACKEND=smtp`, `SMTP_HOST=smtp.zoho.com` (or `smtppro.zoho.com` for a paid domain plan), `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD` (an app password) and `EMAIL_FROM`. Nothing to build. | Staff invites and password resets on a live system |
| _(new)_ **Voice calls** | You plan to use **Zoho Voice**: confirm with Zoho that it can place ordinary phone calls to Nigerian numbers from its API, and the price per minute. If not, Termii voice or Africa's Talking. Decide which languages to offer (English plus Pidgin, Yoruba, Hausa, Igbo?) and whether recorded voices or text-to-speech. | Voice call reminders |
| _(new)_ **Platform bank account** | A business account for SchoolPay itself (not a school's). Set `PLATFORM_BANK_NAME`, `PLATFORM_ACCOUNT_NUMBER` and `PLATFORM_ACCOUNT_NAME` so subscription invoices tell schools where to pay. | Schools paying their subscription |
| _(new)_ **Backups** | Off-site storage (any S3-compatible bucket or similar) and an [age](https://age-encryption.org) key pair. The private key and the `FIELD_ENCRYPTION_KEYS` are kept by you, off the server, apart from each other. See `docs/DEPLOYMENT.md` → Backups. | Recovering from a lost server |
| _(new)_ **Error monitoring** (optional) | A Sentry project; set `SENTRY_DSN`. | Hearing about errors before schools do |
| _(new)_ **Support email** | A mailbox schools and parents can write to; set `SUPPORT_EMAIL` (shown on the website and privacy notice). | Parents' data requests |
| **Hosting and domain** | Where the app will run (a VPS or a managed platform), plus the domain. | Going live |

## Validate with schools (PLAN.md §7)

- [ ] Talk to about 10 bursars and proprietors: how they collect fees today,
      what it costs them, what they'd pay.
- [ ] Find one **pilot school** willing to run a term on it.
- [ ] Get anonymised sample fee schedules and student spreadsheets.

## Legal and compliance (NDPA 2023)

- [ ] Register with the NDPC as a data processor (children's data).
- [ ] A Data Processing Agreement template for schools to sign.
- [ ] Privacy policy and terms of service for the website and parent portal. _(new)_ The sign-up page asks schools to accept the terms, so these are needed before sign-up goes public (or set `SIGNUP_ENABLED=false` until then).
- [ ] _(new)_ Decide how long schools may keep downloaded debtor lists (they
      contain parents' phone numbers) and say so in the school agreement.

## Before going live

- [ ] An independent penetration test of the deployed system: give the tester `docs/PENTEST_CHECKLIST.md`.
- [ ] _(new)_ Do one practice restore from a backup (`deploy/restore.sh`) before the first school goes live, then monthly.
- [ ] Decide who holds the encryption keys and where their backup lives
      (separately from database backups).
- [ ] Create the first platform admin on the live system
      (`python -m app.cli create-platform-admin`).

## Done

- Pricing model chosen: ParentPay's (2026-10-18).
