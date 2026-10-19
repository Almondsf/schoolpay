# Owner to-do list

Things only you (Oluwafemi) can do: decisions, accounts, paperwork and people.
The build carries on without them, but each blocks something real. Kept up to
date as the build goes on; newest additions are marked _(new)_.

Last updated: 2026-10-18 (onboarding and platform billing done)

## Decisions

- [ ] _(new)_ **Set the actual prices.** The model is decided (ParentPay's:
      set-up fee + annual licence + per pupil per year, billed termly, free
      for parents; PLAN.md §3.4). The built-in "Standard" plan has
      placeholder amounts; change them in the platform console (Plans)
      before selling.
- [ ] **Product name.** "SchoolPay" is a placeholder; check the name and
      domain are free and the trademark is clear. _Blocks:_ branding, sender
      IDs, WhatsApp display name, domain.

## Accounts to open

| Account | What to do | Blocks |
|---|---|---|
| **Paystack** (business) | Open the account, get test keys, then ask Paystack what approval/KYC **Dedicated Virtual Accounts** need for a platform with subaccounts (PLAN.md §3.2). Send me the answer. | Per-student account numbers on real money |
| **Flutterwave** | Optional second gateway. Permanent virtual accounts need each customer's BVN, so we use it for card payments only unless they offer another route. | Card payments via Flutterwave |
| **Meta WhatsApp Business** | Create a WhatsApp Business account and phone number in Meta Business Manager, a permanent access token, and submit the three message templates in `docs/DEPLOYMENT.md` ("Messages to parents") for approval. | WhatsApp receipts and reminders (SMS is used until then) |
| **Termii** (SMS) | Open an account, register a sender ID (up to 11 characters, e.g. the product name), and enable the DND route. | Parent sign-in codes and SMS messages on a live system |
| **Email sending** | Pick a provider (e.g. Postmark, Amazon SES, Zoho) and a sending domain. | Staff invites and password resets on a live system |
| _(new)_ **Platform bank account** | A business account for SchoolPay itself (not a school's). Set `PLATFORM_BANK_NAME`, `PLATFORM_ACCOUNT_NUMBER` and `PLATFORM_ACCOUNT_NAME` so subscription invoices tell schools where to pay. | Schools paying their subscription |
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

- [ ] An independent penetration test of the deployed system.
- [ ] Decide who holds the encryption keys and where their backup lives
      (separately from database backups).
- [ ] Create the first platform admin on the live system
      (`python -m app.cli create-platform-admin`).

## Done

- Pricing model chosen: ParentPay's (2026-10-18).
