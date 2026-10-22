# Owner to-do list

Things only you (Oluwafemi) can do: decisions, accounts, paperwork and people.
The build carries on without them, but each blocks something real. Kept up to
date as the build goes on; newest additions are marked _(new)_.

Last updated: 2026-10-22 (live site on Render + Netlify; Termii account opened)

## _(new)_ The live site (2026-10-22)

- [ ] **Change the database password on Render, now.** It was pasted into a chat. Render →
      Postgres → reset the password, then put the new **Internal** URL in the API's
      `DATABASE_URL` (and your local copy of the external URL, if you keep one).
- [ ] **Change the Redis Cloud password** for the same reason (Redis Cloud → database →
      Security), then update `REDIS_URL` and `RATE_LIMIT_STORAGE_URL` on Render.
- [ ] **Render's free Postgres is deleted 30 days after it was made** (made about 20 Oct, so
      around **19 Nov 2026**). Before then, or before any school relies on the site, move to
      the paid database and API plans and a separate background worker (`docs/DEPLOYMENT.md`).
      On the free plan the API sleeps after 15 idle minutes, so reminders pause.
- [x] The API runs migrations, the worker and the API itself from one Docker Command:
      `sh bin/start-all-in-one.sh` (2026-10-22).
- [ ] **Demos:** until Termii is live, demo from your laptop (parent sign-in has a "Fill in
      the code" button there). See `docs/HANDOVER.md` → Running it on this machine.

## Payment providers (2026-10-20)

- [ ] **Paystack "Dedicated NUBAN"** (each pupil's own account number) is not enabled on your
      Paystack business yet: email support@paystack.com to request it. Until then, parents pay
      by card or bank transfer on Paystack's checkout page.
- [x] **Provider decided: Paystack** (2026-10-20). Bachs is built in and can be switched on
      later from Platform → Providers (it needs its keys), but isn't configured. Reason: Bachs
      can't give each pupil their own account number.
- [ ] **Before going live:** generate fresh Paystack keys (the test key was shared in a chat)
      and set the live key and webhook URL as in `docs/DEPLOYMENT.md`.

## Decisions

- [ ] _(new, on hold)_ **Announcements to parents** (2026-10-22): decide who pays for the messages
      (prepaid credits per school, or an allowance in each plan), your price per SMS page, and whether
      urgent announcements reach parents who turned off reminders. Talk to a few schools first. Ask
      Termii for their current rate card (generic and DND routes).

- [ ] _(new)_ **Set the actual prices.** The model is decided (ParentPay UK's:
      set-up fee + annual licence + per pupil per year, billed termly, free
      for parents; PLAN.md §3.4). The built-in "Standard" plan has
      placeholder amounts; change them in the platform console (Plans)
      before selling.
- [ ] _(new)_ **Product renamed to WardPay** (2026-10-22). Still to do:
      - check nobody else trades as WardPay (a quick search and the Nigerian trademark register),
        and get the domain (e.g. wardpay.ng / wardpay.com.ng);
      - _(new)_ on **Render**, set `APP_NAME=WardPay` (the website header and the names in emails,
        texts and receipts come from it) and `MFA_ISSUER=WardPay`; set `FRONTEND_URL` to
        `https://wardpay.netlify.app` and `CORS_ORIGINS` to the same address (links in emails and
        texts are built from it); `EMAIL_FROM` / `TERMII_SENDER_ID` when those are set up; then
        Manual Deploy;
      - on **Netlify**, set `VITE_APP_NAME=WardPay` if it's set there. The site is renamed to
        wardpay.netlify.app (done 22 Oct); receipts printed before that carry a QR code to the
        old address, which no longer works;
      - the Render service keeps its address (schoolpay-a97t.onrender.com) unless you make a new
        service; nobody sees it, Netlify forwards to it.

## Accounts to open

| Account | What to do | Blocks |
|---|---|---|
| **Paystack** (business) | Open the account, get test keys, then ask Paystack what approval/KYC **Dedicated Virtual Accounts** need for a platform with subaccounts (PLAN.md §3.2). Send me the answer. | Per-student account numbers on real money |
| **Flutterwave** | Optional second gateway. Permanent virtual accounts need each customer's BVN, so we use it for card payments only unless they offer another route. | Card payments via Flutterwave |
| **Meta WhatsApp Business** | _(new)_ You plan to use **Zoho** for WhatsApp: Zoho Voice can send WhatsApp messages (approved templates) for you, but **not WhatsApp calls**, and Meta reportedly doesn't allow business-started WhatsApp calls in Nigeria. Either way: create a WhatsApp Business account and phone number in Meta Business Manager, a permanent access token, and submit the three message templates in `docs/DEPLOYMENT.md` ("Messages to parents") for approval. _(new)_ The bill and reminder templates' last two parameters are now the parent's family payment link and what the money is for (e.g. ": Tuition ₦130,000, PTA levy ₦5,000"); see `docs/DEPLOYMENT.md`. | WhatsApp receipts and reminders (SMS is used until then) |
| **Termii** (SMS) | _(new)_ **Account opened** (2026-10-22, ₦30 balance; KYC documents still to come). Still to do: request a **Sender ID** (up to 11 characters, e.g. `WardPay`; Termii approves it), ask Termii to **switch on the DND route** (sign-in codes must reach numbers on Do-Not-Disturb), **top up**, then on Render set `SMS_BACKEND=termii`, `TERMII_API_KEY` and `TERMII_SENDER_ID` (exactly as approved). | Parent sign-in codes and SMS messages on a live system |
| **Email sending** | _(new)_ **Zoho Mail chosen** (2026-10-19). Create the mailbox on your domain, then set `EMAIL_BACKEND=smtp`, `SMTP_HOST=smtp.zoho.com` (or `smtppro.zoho.com` for a paid domain plan), `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD` (an app password) and `EMAIL_FROM`. Nothing to build. | Staff invites and password resets on a live system |
| _(new)_ **Voice calls** | **Built** with Africa's Talking (calls in English and Pidgin, read by text-to-speech). To go live: an Africa's Talking account and voice number, the env settings and callback URL in `docs/DEPLOYMENT.md`. If you'd rather use Zoho Voice, confirm its API can call Nigerian numbers and read out text, and it can be added as another provider. **Decide:** who pays for calls (included in the per-pupil fee, or charged to the school per call?); whether to add Yoruba, Hausa and Igbo, which need voices recorded by native speakers (text-to-speech in those languages isn't good enough yet). Listen to a test call in Pidgin before launch: it's read by an English voice. | Phone calls to parents |
| _(new)_ **Platform bank account** | A business account for WardPay itself (not a school's). Set `PLATFORM_BANK_NAME`, `PLATFORM_ACCOUNT_NUMBER` and `PLATFORM_ACCOUNT_NAME` so subscription invoices tell schools where to pay. | Schools paying their subscription |
| _(new)_ **Backups** | Off-site storage (any S3-compatible bucket or similar) and an [age](https://age-encryption.org) key pair. The private key and the `FIELD_ENCRYPTION_KEYS` are kept by you, off the server, apart from each other. See `docs/DEPLOYMENT.md` → Backups. | Recovering from a lost server |
| _(new)_ **Error monitoring** (optional) | A Sentry project; set `SENTRY_DSN`. | Hearing about errors before schools do |
| _(new)_ **Support email** | A mailbox schools and parents can write to; set `SUPPORT_EMAIL` (shown on the website and privacy notice). | Parents' data requests |
| **Hosting and domain** | _(new)_ Hosting chosen: the API, worker, database and Redis on **Render**, the web app on **Netlify** (https://wardpay.netlify.app, renamed from parentpay.netlify.app on 22 Oct); setup in `docs/DEPLOYMENT.md`. Still to do: buy a domain and add it on Netlify, then update `FRONTEND_URL` and `CORS_ORIGINS` on Render. | Going live |

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

- Pricing model chosen: ParentPay UK's (2026-10-18).
