# System overview

This document explains what SchoolPay does and how it is built. It is written
for a developer joining the project; the first sections assume no school-finance
background. For what each kind of user needs, see [USER_STORIES.md](USER_STORIES.md).
For running it in production, see [DEPLOYMENT.md](DEPLOYMENT.md). Security and
data protection are covered in [SECURITY.md](SECURITY.md) and [PRIVACY.md](PRIVACY.md).

**Contents**

1. [What the system is](#1-what-the-system-is)
2. [School words you will meet](#2-school-words-you-will-meet)
3. [The three faces of the app](#3-the-three-faces-of-the-app)
4. [Architecture](#4-architecture)
5. [Tenancy: one deployment, many schools](#5-tenancy-one-deployment-many-schools)
6. [Accounts, sign-in and roles](#6-accounts-sign-in-and-roles)
7. [Audit trail](#7-audit-trail)
8. [School setup: calendar, classes, students](#8-school-setup-calendar-classes-students)
9. [Fees and invoices](#9-fees-and-invoices)
10. [Payments](#10-payments)
11. [Receipts](#11-receipts)
12. [Parent portal](#12-parent-portal)
13. [Messages to parents](#13-messages-to-parents)
14. [Reports and the dashboard](#14-reports-and-the-dashboard)
15. [The platform: plans, subscription billing, sign-up](#15-the-platform-plans-subscription-billing-sign-up)
16. [Data protection: export, erasure, retention, monitoring](#16-data-protection-export-erasure-retention-monitoring)
17. [Backend layout and conventions](#17-backend-layout-and-conventions)
18. [Background jobs and the outbox](#18-background-jobs-and-the-outbox)
19. [API](#19-api)
20. [Configuration](#20-configuration)
21. [Frontend](#21-frontend)
22. [Testing](#22-testing)
23. [Known gaps](#23-known-gaps)

---

## 1. What the system is

SchoolPay is a multi-tenant SaaS for Nigerian private schools. One deployment
serves many schools. Each school uses it to bill fees, collect payments and talk
to parents.

The core idea: every student gets a **dedicated virtual bank account number**
(from Paystack or Flutterwave). Any transfer into that number belongs to that
student, so payments are matched automatically. Nobody has to guess from a
narration like "FEES".

The platform **never holds school money**. Payments settle straight into the
school's own bank account through gateway subaccounts. The platform's fee is
taken through the split.

## 2. School words you will meet

| Term | Meaning |
|---|---|
| **Session** | The school year, e.g. 2026/2027. Runs September to July. |
| **Term** | A third of a session (1st, 2nd, 3rd). Fees are billed per term. |
| **Class level** | A year group, e.g. JSS1, Primary 4. |
| **Arm** | A stream within a class level, e.g. JSS1 A, JSS1 Gold. |
| **Proprietor** | The school's owner. Watches collections; usually the buyer. |
| **Bursar** | The school's finance officer. Bills, reconciles and chases fees. |
| **Guardian** | A parent or other adult responsible for a student. One guardian can have several children in one school. |
| **Arrears** | Unpaid fees from earlier terms, carried forward. |
| **Teller** | A bank deposit slip. Parents pay at the bank and bring the teller to school. |
| **Kobo** | 1/100 of a naira. All amounts are stored in kobo as integers. |

## 3. The three faces of the app

| Area | Path | Who |
|---|---|---|
| Staff console | `/console` | Proprietor, bursar, school admin; platform admins |
| Parent portal | `/parent` | Parents (mobile-first; phone + OTP sign-in) |
| Receipt check | `/verify/...` | Anyone holding a receipt (public, no sign-in) |

## 4. Architecture

```
 Browser (React SPA) ──HTTP /api/v1──▶ FastAPI (uvicorn) ──▶ PostgreSQL
                                           │
                                           └─ enqueue ─▶ Redis ─▶ Celery worker / beat
                                                                     │
                                       Paystack / Flutterwave / WhatsApp / SMS / email
```

- **Backend** (`backend/`): FastAPI, SQLAlchemy 2.0 (typed `Mapped[]` models),
  Alembic, PostgreSQL via psycopg 3, Celery + Redis.
- **Frontend** (`frontend/`): React 18, TypeScript, Vite, react-query, react-router.
- In production, nginx serves the built frontend and proxies `/api` to the
  backend, so the browser sees one origin.

## 5. Tenancy: one deployment, many schools

Isolation between schools is the most important property of the system. It is
enforced in one place, `app/core/tenancy.py`:

- Every school-owned model inherits **`TenantMixin`**, which adds a non-null
  `school_id` foreign key.
- Every database session declares a **scope** before it touches tenant data:
  - `scope_to_school(session, school_id)`: used for staff and parent requests and
    for per-school jobs. A SQLAlchemy `do_orm_execute` hook adds
    `school_id = :id` to every SELECT, UPDATE and DELETE on tenant models
    (including relationship loads and `session.get`). A `before_flush` hook
    stamps `school_id` on new rows and refuses rows for another school or
    changes to `school_id`.
  - `scope_platform_wide(session)`: platform admin and cross-school system jobs.
    Nothing is filtered, and new rows must set `school_id` explicitly.
- A session with **no scope** that touches tenant data raises `TenancyError`.
  Forgetting to scope fails closed; it doesn't leak.
- Routers never filter by school by hand. New rows never take `school_id` from
  the request body.
- Raw SQL (`text()`) bypasses the hooks and must not be used on tenant tables.

Every endpoint has a test proving school A can't read or write school B's data,
including through IDs in URLs and request bodies.

`User` is the one exception to the mixin: sign-in has to find a user before
the school is known. Staff queries therefore pin `school_id` explicitly in
`staff_service`, and the cross-school tests cover each staff endpoint.

## 6. Accounts, sign-in and roles

**Who signs in how**

| Person | Signs in with | Belongs to |
|---|---|---|
| Proprietor, bursar, school admin | Email + password (+ authenticator code) | One school |
| Platform admin (us) | Email + password + authenticator code | No school |
| Parent | Phone + one-time code (parent portal) | Their children's school |

Staff accounts are `User` rows (`app/modules/accounts`). There's no public
sign-up: a platform admin creates a school and its first proprietor, and
proprietors invite the rest of their staff. Invitees get an email link to
choose a password. The very first platform admin is created from the command
line (`python -m app.cli create-platform-admin`), which prints a one-time link.

**Sessions.** Signing in creates an `AuthSession` row (one per device) and returns:

- a 15-minute **access token** (JWT). It carries the session id, and every
  request checks that the session is still live, so sign-out, deactivation,
  role changes and password changes take effect immediately.
- a **refresh token** in an httpOnly, `SameSite=Strict` cookie scoped to
  `/api/v1/auth`. It rotates on every use. Presenting an old one again means
  it was copied, so the whole session is revoked (`RotatedRefreshToken`).

Every token has a `typ` claim (`access`, `mfa`, `password`) and is accepted
only for its own purpose.

**Two-factor sign-in (TOTP).** Proprietors, bursars and platform admins must
enrol before they can do anything else. The API answers
`403 mfa_enrolment_required`, and the console sends them to their account page.
For enrolled users, the password step returns a 5-minute MFA ticket instead of
tokens. A code from the app (each code works once) or a single-use recovery
code completes the sign-in. A proprietor can reset a staff member's MFA when
they lose their phone.

**Passwords** are hashed with argon2. They must be 10+ characters, not a
common password, and not contain the user's name or email. Invite and reset
links are signed tokens tied to the current password hash, so using one
retires it.

**Roles and capabilities** (`app/modules/accounts/permissions.py`). Endpoints
check capabilities, never role names:

| Capability | Proprietor | Bursar | School admin |
|---|:-:|:-:|:-:|
| school.view / staff.view | ✓ | ✓ | ✓ |
| school.manage (profile, sessions, classes) | ✓ | | ✓ |
| staff.manage | ✓ | | |
| audit.view | ✓ | ✓ | |
| students.view | ✓ | ✓ | ✓ |
| students.manage | ✓ | | ✓ |
| fees.view | ✓ | ✓ | ✓ |
| fees.manage, invoices.issue | ✓ | ✓ | |
| payments.view, payments.record | ✓ | ✓ | ✓ |
| payments.approve, payments.reverse | ✓ | ✓ | |
| reports.view, messaging.manage | ✓ | ✓ | |

Platform admins hold only `platform.manage`: they manage schools but can't
read a school's data through the school endpoints.

Staff rules: nobody can change their own role or deactivate themselves; a
school always keeps at least one active proprietor; staff email addresses are
unique across the platform.

**How a request is authorized** (`app/modules/accounts/deps.py`):
`get_principal` checks the access token, then loads the session, user and
school status in one query. It also scopes the database session to the user's
school (or platform-wide for platform admins). `require(capability)` then
checks MFA enrolment and the capability. Suspended schools are refused at both
sign-in and on every request.

## 7. Audit trail

`app/modules/audit` records every sign-in (including failures), session reuse,
password and MFA change, staff change, and school change. Every money action
will be recorded too. Each entry stores the actor, action, target, before/after
values, IP, user agent and request id. Values pass through redaction first.

- `audit_log` belongs to a school (a `TenantMixin` table, scoped like any other
  school data). `platform_audit_log` holds events with no school.
- `audit.record()` adds the entry to the caller's transaction, so an action
  and its audit entry commit or roll back together.
- Both tables are **append-only in the database**: triggers refuse UPDATE,
  DELETE and TRUNCATE, whatever the application does.

## 8. School setup: calendar, classes, students

**Calendar** (`app/modules/schools`). An `AcademicSession` ("2026/2027") has
exactly three `Term`s, whose dates must fall inside the session and not
overlap. Exactly one term per school is **current** (a partial unique index
enforces it). The console defaults to it, new students are enrolled into it,
and later chunks bill it.

**Classes.** A `ClassLevel` ("JSS 1", "Primary 4") has one or more `Arm`s
("A", "Gold"). `rank` orders levels for promotion and can be changed
(`PUT /class-levels/order`). Levels and arms are retired (`is_active`), never
deleted, because enrolment history refers to them.

**Students** (`app/modules/students`). A `Student` has an admission number
(unique per school), names, and optional gender and date of birth; `status`
is `active`, `withdrawn` or `graduated`. Where a student is each term is an
`Enrollment` (one row per student per term). That keeps class history, and
each term's fees use that term's class. A composite foreign key
(`arm_id`, `class_level_id`) → `arms(id, class_level_id)` guarantees an
enrolment's level matches its arm.

**Guardians.** One `Guardian` can have several children at the school
(`GuardianStudent`), and each child has exactly one **primary** guardian
(partial unique index), who receives fee notices. Guardians are matched by
phone, so siblings share one guardian. Phones are normalised to E.164
(`app/core/phone.py`: `0803…`, `234803…` and `+234 803…` are the same
number). Phone and email are **encrypted**, and `phone_index` (a keyed blind
index) is what lookups and the per-school uniqueness constraint use. The same
parent at two schools is two separate guardian rows, one per school.
Consent is recorded per guardian (`consent_at`, `consent_method`). Staff must
confirm the school has it before guardian details are saved.

**Spreadsheet import** (`students/importer.py`, `POST /students/import`).
It reads `.xlsx` or `.csv` and matches headers loosely: "Adm No", "Reg No"
and "Admission Number" are all the admission number, and
Guardian/Parent/Father/Mother columns are recognised. Classes can be one
column ("JSS 1A", "Primary 4 Gold") or class + arm. The whole file is
validated first and every problem is reported with its row number.
`dry_run=true` previews without saving. A real run imports the valid rows.
Re-importing is safe: existing admission numbers are skipped, or updated with
`mode=update`. **It runs a fixed number of queries whatever the file size**
(tests assert this). 500 students import in about 4 seconds. Limit: 5,000
rows per file. `GET /students/import-template` gives an Excel template that
lists the school's classes.

**Moving to the next term** (`students/promotion.py`,
`POST /terms/{id}/roll-forward`). Within a session, enrolments are copied.
Between sessions (`promote=true`), students move to the next active level by
rank, into the arm with the same name (or the only arm). Students in the top
level graduate, and anyone who can't be placed is listed. Already-enrolled
students are left alone, so it's safe to re-run. `dry_run` previews it.

**The school at a glance** (`students/summary.py`, `GET /school/roll`,
`students.view`). The dashboard for staff who don't see money (school admins):
pupils on roll, placed this term, joined this term (first class is this
term's), left since the term started, parents, pupils per class and arm (with
boys/girls) in promotion order, and **records needing attention**: pupils with
no class this term, no parent, no parent whose consent is recorded, or no
active account number. Always three queries. The "missing" conditions live in
one place (`students.service.lacks`) and also filter the list:
`GET /students?missing=parent|consent|account_number` (and `unplaced=true`),
so each count on the dashboard links to exactly those pupils
(`/console/students?show=…`, `?class=…`).

## 9. Fees and invoices

Module: `app/modules/fees`. All amounts are **integer kobo** (`BigInteger`);
the frontend parses naira text to kobo without floats.

**Setup** (`setup_service.py`):

- `FeeItem`: something the school charges for (Tuition, School bus, PTA levy),
  with a category. Items are retired, not deleted.
- `FeeSchedule`: what an item costs for one class level in one term. An entry
  can be **optional** (bus, lunch): optional items are billed only to students
  opted in for that term (`StudentFeeChoice`). Schedules are set in bulk
  (`PUT /terms/{id}/fee-schedule`, a grid in the console) and can be copied
  from another term.
- `Discount`: a percentage (basis points, 1000 = 10%) or a fixed amount, on all
  fees or one item. `manual` discounts are given to chosen students
  (`StudentDiscount`: scholarship, staff child). `sibling` discounts apply
  automatically: students sharing any guardian form a family, the eldest
  (highest class, then lowest admission number) pays in full, and every
  younger child gets the discount.

Changing setup affects invoices generated afterwards only. Issued invoices are
ledger records and are never rewritten.

**The ledger** (`models.py`). An `Invoice` belongs to one student and is one of:
`term` (the term's fees; at most one live one per student per term),
`opening_balance` (debt from before the school joined SchoolPay) or `adhoc` (a
one-off charge). Its total is the **sum of its `InvoiceLine`s**: `fee` lines
(positive), `discount` lines (negative) and `adjustment` lines (later
corrections, either sign, with a reason). Rules enforced by the database:

- Lines are **append-only**: a trigger refuses UPDATE, DELETE and TRUNCATE. A
  mistake is fixed with a new adjustment line, so the history stays visible.
- An invoice can never total below zero (deferred constraint trigger, checked
  at commit).
- Voiding is allowed only while nothing has been paid. A voided invoice keeps
  its lines but no longer counts as owed.
- Invoice numbers are per school and **gapless**: `app/core/sequences.py`
  reserves a block in one statement under a row lock, and a rollback gives the
  numbers back.

**Balances are never stored.** Every figure (an invoice's paid and balance, a
student's billed, paid and owed, a term's billed, collected and outstanding)
is computed from lines and payments. `ledger.paid_subquery()` is the one place
the "paid" side comes from. Arrears carry forward naturally: earlier unpaid
invoices stay open, the student's "owed" figure includes them, and payments
(next chunk) clear the oldest first. Tests check that the term summary, the
invoice list and the sum of all lines agree.

**Generating a term's invoices** (`invoicing.py`,
`POST /terms/{id}/invoices/generate`): one click invoices every active student
enrolled that term. Each gets their class's compulsory fees, the optional items
they're opted in to, and their discounts (capped so an invoice never goes
negative). Students already invoiced are skipped, so it's safe to re-run after
adding students. `dry_run` returns a preview with totals per class and any
classes with no fees set. With `notify_parents`, each main parent is also sent
a fee notice (section 13), and the response says how many were queued. It runs
**a fixed number of queries** whatever the number of students (tested), and 60
invoices take about 0.4 s.

**Charges after the term starts** (`charges.py`; capability `invoices.issue`):

- **Class charges** (`POST /terms/{id}/class-charges`, with a dry run that
  lists every pupil). A fee for whole classes, such as WAEC for SSS 3. It's added
  to the term's fee list for those classes (so later bills include it) and as a
  line on every bill already issued there. Credit already paid covers it.
  Excluded pupils get an exemption, so regenerating can't add it back. The same
  fee twice for one class is refused.
- **Exemptions** (`StudentFeeExemption`; `GET/POST /students/{id}/exemptions`,
  `DELETE .../exemptions/{fee_item_id}?term_id=`). A pupil doesn't pay a class
  fee this term. Term generation leaves the item out. If the pupil is already
  billed, an adjustment line takes it off; that's refused if more has been paid
  than the bill would then total. Removing the exemption charges the class
  price again.
- **Individual extras** (`POST /students/{id}/charges`). One pupil's uniform or
  textbooks, with a quantity, priced from the item's standard price
  (`FeeItem.default_amount_kobo`, the school's price list) or typed in. It goes
  on the pupil's term bill, or on a one-off bill if they haven't been billed
  this term.

Each of these is a fixed number of statements however many pupils it touches,
and each is audited.

**Payment plans** (`plans.py`, `POST /invoices/{id}/plan`,
`/plan/cancel`; capability `invoices.issue`). The school can agree with a
parent to pay what's left on a bill in 2 to 12 dated instalments that add up
to exactly what's owed (`covered_kobo`). The first instalment can't be in the
past, and dates must increase.

Money is applied as always (oldest debt first). Progress comes from the ledger:
what's been paid on the bill since the plan started (paid now minus
`paid_at_start_kobo`) covers the instalments in order. A plan changes two
things:

- **When the bill counts as late.** Reports, the debtors list, aging and the
  "overdue" filter use the next unpaid instalment's date
  (`progress_cte()`, a materialized CTE).
- **What reminders ask for.** Reminders fall around instalment dates and ask
  for that instalment: `{balance}` becomes the instalment amount, and
  `{due_phrase}` becomes " (instalment 2 of 3, due 15 Nov 2026)".

A plan is never edited; it's cancelled (with a reason, audited) and a new
one made. Instalments are append-only, there's one live plan per bill
(partial unique index), and voiding the bill ends its plan. The bill's page
shows the schedule; the parent portal shows it on the child's bills; the
debtors list marks "On a plan".

## 10. Payments

Module: `app/modules/payments`.

**Money never touches the platform.** Each school connects its own bank account
(`PUT /payment-settings`, proprietor only). The gateway confirms the account
name and creates a **subaccount**. Every card payment and every transfer into a
student's account number is split by the gateway: the school's share settles
to the school, and the platform fee (`PLATFORM_FEE_BP`, recorded per school)
settles to us. The school bears the gateway's own charges.

**Gateways** (`gateways/`): Paystack, Flutterwave and a fake one share one
interface: list banks, resolve an account, create a subaccount, create a
virtual account, start a checkout, verify a transaction, parse a webhook.
`get_gateway()` only hands out gateways enabled in `PAYMENT_GATEWAYS`. Calls go
through `app/core/outbound.py`, which logs every call with secrets and card
details redacted. The **fake gateway** issues account numbers, checkout links
and signed webhooks in-process, so the whole flow works in development and
tests without real money. Production refuses it. (Flutterwave requires a BVN
for permanent virtual accounts, which we don't collect, so it offers card
payments only. See PLAN §3.2.)

**How money arrives:**

| Route | Flow |
|---|---|
| Transfer into a student's account number | The gateway sends a webhook. We verify its signature, store it in the `webhook_events` inbox (a unique key ignores repeats), and enqueue processing via the outbox. The worker asks the gateway to verify the transaction, finds the `VirtualAccount` it was paid into, and records the payment. |
| Card (payment link) | Staff create a link (`POST /students/{id}/payment-link`); a `PaymentIntent` holds our reference. The parent pays on the gateway's page and comes back to `/pay/complete`, which calls `POST /payments/verify`. The webhook arrives too. Both end in `checkout.confirm()`, which locks the intent, verifies with the gateway, and records the payment once. |
| Card (family link) | Each parent has one link (`/pay/f/<token>`) for all their children, no sign-in: see below. The parent sets an amount per child; one card charge becomes one payment per child. |
| Cash, teller, POS, direct transfer | Staff record a `ManualPayment`; **a different person** approves it (maker-checker, also a database constraint), and only then is it a payment. Rejections keep a reason. |

**The ledger.** `Payment` rows are receipts (positive) or reversals (negative,
one per payment, pointing at the original). `Allocation` rows apply payments to
invoices. Both tables are append-only (database triggers), and deferred
constraint triggers guarantee that a payment is never allocated beyond its
amount and an invoice is never paid beyond its total. A payment is recorded
once per gateway reference (unique constraint), however many times the webhook
or redirect arrives.

**Allocation** (`allocation.py`). A student's unallocated money pays their
oldest debt first: the opening balance, then invoices in issue order. Whatever
is left is the student's **credit** (payments minus allocations, never stored),
and new invoices consume it automatically when generated. Allocation locks the
students' rows, so concurrent payments for one student queue up. It runs a
fixed number of queries for any number of students.

**Reversals** (`POST /payments/{id}/reverse`, bursar or proprietor) add a
negative payment and negative allocations that undo the original's. The debt
it paid is owed again, and nothing is edited or deleted.

**Virtual accounts.** `POST /virtual-accounts/provision` queues account numbers
for active students without one (batches of 100, background task, one gateway
call per student, one insert per batch). The gateway needs an email per
customer, so a student without a parent email gets a routing address
(`student-<id>@<school>.schoolpay.ng`). Each student's number is shown on
their page and, later, in the parent portal.

**Family payment links** (`family_links.py` for the link, `family.py` for the
page and checkout). One link per parent (`FamilyPayLink`: one live link per
guardian by a partial unique index), made the first time a bill notice or
reminder needs it (`{pay_link}`) or when staff open it
(`GET /guardians/{id}/pay-link`, `payments.record`). It's a bearer token: the
public page (`GET /public/pay/{token}`) shows only the school, each current
child's **first name**, class, what's owed, what's due now (on a payment plan,
up to the next instalment) and their account number, never surnames. The token
is stored as a keyed hash for lookup plus an encrypted copy so every message
carries the same link until it expires (`FAMILY_LINK_DAYS`, default 120; a new
one is made on the next message). Staff can replace it
(`POST /guardians/{id}/pay-link/replace`, audited), and erasing the parent ends
it. Unknown, expired and replaced links get the same answer.

Paying by card from the link (`POST /public/pay/{token}/checkout`): the parent
chooses the amount per child (zero skips a child). Each share must be at least
₦1,000 (or everything that child owes) and at most what that child owes: paying
ahead is by transfer. The intent holds one `PaymentIntentItem` per child. When the
gateway confirms, `confirm()` records one card payment per child with gateway
reference `<reference>-<n>` (so repeats are still ignored), each applied oldest
bill first with its own receipt. If the gateway's total isn't what was asked, the
intent is parked as `needs_review`; nothing is split by guesswork. All recording
goes through `service.record_payments()`, which handles any number of payments
in a fixed number of statements (`record_payment()` is the one-payment wrapper).

**Failures.** If the gateway is down while processing, the event stays
`received`. The `payments.requeue_webhooks` beat task retries every 5 minutes,
and after 10 attempts the event is parked as `failed` for an operator.
Transfers to an unknown account, or in a currency other than NGN, are parked
as `needs_review` and never guessed at.

## 11. Receipts

Module: `app/modules/receipts`.

- **One receipt per payment**, created by `record_payment()` in the same
  transaction as the payment, so neither exists without the other. Reversals
  don't get receipts; they make the original receipt show as reversed.
- **Numbered per school with no gaps** (`app/core/sequences.py`, sequence
  `receipt`): the number is reserved in the payment's transaction, so a
  rollback gives it back.
- **Immutable** (append-only trigger). Each receipt keeps a **snapshot**: school
  name and contact, student name, admission number and class at the time, method
  and reference, which invoices it paid, credit left over, and the balance still
  owed. A reprint years later reads the same even if the student is renamed.
- **PDF** (`pdf.py`, reportlab, `GET /receipts/{id}/pdf`): an A4 page with the
  amount in figures and in words (`words.py`: "Seventy-five thousand, five
  hundred naira, fifty kobo only") and a QR code.
- **Public verification.** The QR code links to `/verify/<code>`. The code is a
  random 128-bit token, so receipts can't be enumerated by number.
  `GET /public/receipts/{code}` (no sign-in, rate limited) returns only the
  school, receipt number, amount, date, status (`valid` or `reversed`) and the
  student's initials. A made-up receipt matches no code and is reported as not
  genuine.

## 12. Parent portal

Module: `app/modules/parents`. Web app: `/parent` (mobile first).

**Signing in with a phone number.** Parents are guardians, found by the blind
index of their phone.

1. `POST /parent/auth/request-code {phone}`: if the number belongs to a guardian
   at an active school, a 6-digit code is texted to it. The answer is the same
   whether or not the number is registered, so it can't be used to find out who
   is. Only a keyed HMAC of the code is stored (`parent_otps`). A new code
   retires the old one. The code reaches the SMS task through the outbox
   **encrypted**, and the SMS backends never log message text.
2. `POST /parent/auth/verify {phone, code}`: valid for `OTP_MINUTES` (5), with at
   most `OTP_MAX_ATTEMPTS` (5) tries before the code is burned. Requests and
   attempts are rate limited per phone and per IP.
3. A parent with children at more than one school gets a short-lived ticket and
   chooses (`POST /parent/auth/choose-school`). Each option lists the children's
   first names, so similar school names can be told apart.

Each **portal session** (`parent_sessions`) is bound to one guardian record,
and so to one school. Parent access tokens have their own type (`parent`), so
staff and parent tokens can't stand in for each other. The refresh token is an
httpOnly cookie scoped to `/api/v1/parent/auth`. It rotates on every use, and
presenting the previous one again (a copied cookie) ends the session.

**What a parent sees** (`portal.py`): every lookup goes through the guardian's
links (`GuardianStudent`), so another child's ID is "not found", even in the
same school. `GET /parent/home` returns each child's class, amount owed, credit
and account number in a fixed number of queries. A child's page lists bills
(with lines), payments and receipts (PDF download). "Pay by card" starts a
checkout with the parent's email, or a routing address if the school has none,
and returns to `/pay/complete`.

**Text messages** (`app/modules/notifications/sms.py`): `SMS_BACKEND=console`
logs only a masked number and the message length; `termii` sends through
Termii on the DND route (needed for sign-in codes); `memory` is for tests.
Parents can turn off bill notices and reminders from the portal home page (see
section 13).

## 13. Messages to parents

Module: `app/modules/notifications`. Console page: **Messages** (bursar and
proprietor, capability `messaging.manage`).

**What gets sent.** Three kinds of message (`MessageKind`), always to the
child's **main parent** (the primary guardian):

| Kind | When | Opt-out applies? |
|---|---|---|
| `receipt` | Every payment recorded (`payments/service.record_payment` queues it) | No: a receipt is a record of the parent's own money |
| `fee_notice` | Bills generated with "send each parent a message" ticked (`notify_parents`) | Yes |
| `reminder` | On the school's schedule, or "Send reminder" on a student's page | Yes |
| `call_reminder` | A phone call beside an **overdue** scheduled reminder, for parents who prefer calls | Yes |
| `call_receipt` | A phone call beside each receipt, for parents who prefer calls | No |

**Phone calls** (`voice.py`), for parents who don't read messages easily. They
go only to parents marked "prefers calls" (staff on the pupil's page, or the
parent in the portal; `Guardian.prefers_calls` and `language`: English or
Pidgin), and only at schools that switched calls on (`ReminderSettings.calls_enabled`,
with a cap per bill, `max_calls_per_invoice`). A call never replaces the text:
the parent still gets the reminder or receipt, so a POS agent can read the
account number. A reminder call says what's owed and reads the child's account
number digit by digit, in groups, twice; a receipt call confirms the amount and
what's left. Amounts are said in English words (how Nigerians usually say them,
even in Pidgin). Calls are made only between `CALL_HOURS_START` and
`CALL_HOURS_END` (9:00 to 19:00 Lagos) and re-check at calling time that the
parent still wants calls, the school still has them on, and (for reminders)
that the bill is still owed. The wording is fixed, not school-editable.
Providers (`VOICE_BACKEND`): `console`, `memory` (tests), `africastalking`.
With Africa's Talking we ask it to dial; when the parent answers it fetches the
words from `POST /voice/africastalking` (found by the call's session id, and only
if the number dialled is the parent's), and it reports there how the call ended
(an unanswered call is noted on the message).

**The pipeline** (`service.py`):

1. `queue()` inserts `message_log` rows plus one outbox task per new row, in the
   caller's transaction (one `INSERT … ON CONFLICT DO NOTHING` for any number of
   messages). Every row has a `dedupe_key` (`receipt:<payment>`,
   `notice:<invoice>`, `reminder:<invoice>:<date>`,
   `reminder-manual:<student>:<date>`) that is unique per school, so a retry or
   a repeated trigger can never message a parent twice. A fee notice or reminder
   queued during quiet hours gets an outbox `not_before` of the next morning.
2. `deliver()` (task `notifications.deliver`) **re-checks everything when it
   sends**: the row is still queued, it's younger than `MESSAGE_MAX_AGE_HOURS`
   (otherwise skipped as `too_old`, e.g. after an outage), the parent hasn't
   opted out (`opted_out`), the bill is still owed (`paid`), and it isn't quiet
   hours (otherwise re-queued for the morning). It then fills the school's
   wording with **today's** figures and tries **WhatsApp → SMS → email**,
   recording the channel used, or the errors. After 3 failed attempts the row
   is `failed`; `notifications.retry_unsent` (hourly) retries the ones in
   between.
3. `schedule_reminders()` (hourly, task `notifications.schedule_reminders`)
   picks the schools whose sending hour it is and finds every bill that is
   N days before or after its due date (for a bill on a payment plan, its
   next unpaid instalment's date), per the school's `ReminderSettings`. It
   leaves out bills that are paid, voided or opening balances, students who
   have left, opted-out parents, bills that have reached `max_per_invoice`, and
   bills reminded within `min_gap_days`. All schools are handled in a fixed
   number of queries, and the inserts go in one statement.

**Wording** (`templates.py`): each school can replace the standard wording of
each kind (`message_templates`) using a fixed set of `{placeholders}`. Unknown
placeholders or broken brackets are refused when the wording is saved. The
limit is 640 characters (four SMS segments). `POST /message-templates/preview`
fills a draft with sample details. **WhatsApp** only allows business-started
messages from templates Meta has approved, so WhatsApp always uses the
platform's templates (`WHATSAPP_TEMPLATES`), passing the same values as
parameters in the order given by `WHATSAPP_PARAMETERS`. See
docs/DEPLOYMENT.md.

**Opt-out.** `guardians.reminders_opt_out_at` is set by the parent (portal:
"Send me new bills and payment reminders") or by staff
(`PUT /guardians/{id}/reminders`). Both are audited. Receipts still go.

**Limits.** Quiet hours are `QUIET_HOURS_START`–`QUIET_HOURS_END` (20:00–07:00
Lagos). A school's send hour must be between 7 and 19, and it can send at most
10 reminders per bill, at least a day apart. A manual reminder is limited to
one per student per day, and refused if nothing is owed or the parent opted
out.

**Endpoints:** `GET /messages` (log, filter by kind/status/student),
`GET /message-templates`, `PUT /message-templates/{kind}` (`body: null` resets),
`POST /message-templates/preview`, `GET|PUT /reminder-settings`,
`PUT /guardians/{id}/reminders`, `POST /students/{id}/remind`, and for parents
`GET|PUT /parent/preferences`.

**Channels:** `whatsapp.py` (`WHATSAPP_BACKEND` off | console | memory | cloud),
`sms.py` (`SMS_BACKEND` console | memory | termii), and `app/core/email.py`.
Console backends log only a masked number or the recipient's domain, never the
text.

## 14. Reports and the dashboard

Module: `app/modules/reports`. Console: the **Overview** page (the dashboard)
and **Reports** (debtors, money received). Capability `reports.view`
(proprietor and bursar). Staff without it see the school at a glance instead
(§8), never money.

**Where the numbers come from.** Every figure is built from the ledger: the
invoice lines (`fees.invoicing.totals_subquery`) and payment allocations
(`fees.ledger.paid_subquery`). So reports always agree with invoices, student
accounts and receipts. A payment that is reversed counts on the day it was
reversed. Dates are Lagos dates. A bill on a payment plan is "due" when its
next unpaid instalment is, so a parent keeping to a plan isn't overdue.

| Report | Endpoint | What it shows |
|---|---|---|
| Overview | `GET /reports/overview?term_id=` (default: current term) | This term's billed, collected and outstanding amounts, overall and per class (in promotion order); older debts (earlier terms, brought forward, one-off charges); credit held; how many students owe; money received today, in the last 7 days and per day this term; payment methods; cash and teller entries waiting for approval; and debts by how late they are (not yet due, 1–30, 31–60, 61–90, over 90 days) |
| Debtors | `GET /reports/debtors` (filters: class, minimum owed, overdue only, search; sort: most owed, longest overdue, class, name) | Each student who owes: amount, oldest unpaid due date, days overdue, class, main parent and phone, reminders sent or turned off, last payment (a reversed payment doesn't count). Paginated, with the total owed across the filter |
| Money received | `GET /reports/collections?start=&end=` (at most a year) | Received, reversed and net, per day and per method |

**Downloads.** `GET /reports/debtors/export` and
`/reports/collections/export` take `format=xlsx|pdf` and the same filters.
Excel (`exports.py`, openpyxl) has the school's name, the scope and when it
was produced. Money is exact decimal naira with a number format, and the
money-received workbook has By day, By method and Payments sheets. PDFs
(reportlab, landscape A4) repeat the header row on every page and show
"NGN", because the standard fonts have no naira sign. Text that Excel would
treat as a formula (`= + - @`) gets an apostrophe in front. Downloads are
capped at 5,000 rows, rate limited per school (60 an hour), sent
`Cache-Control: no-store`, and recorded in the activity log
(`reports.exported`: which report, format, row count, scope; never the
contents).

**Performance.** Each report is a fixed number of queries, whatever the size
of the school (tested). The per-invoice and per-student aggregates are
**materialized CTEs** (`_once()` in `service.py`). Right after a bulk insert,
such as generating a term's bills, Postgres's statistics are stale until
autovacuum runs. With plain subqueries the planner then chose nested loops
that redid an aggregate for every row: 18 seconds instead of under one for 60
students. Materializing makes each aggregate run once, whatever the plan.

## 15. The platform: plans, subscription billing, sign-up

Module: `app/modules/platform`. Console: the platform admin's **Overview**,
**Schools**, **Billing**, **Plans** and **Support lookup**; for schools,
**Subscription** (under Setup) and the **Getting started** checklist on the
dashboard; the public **/signup** page.

**Pricing (ParentPay's model, PLAN.md §3.4).** The school pays and parents use
SchoolPay free. A `Plan` has a one-off **set-up fee**, an **annual licence**,
and a **per-pupil fee per year**, billed a third each term for the pupils
enrolled that term (`billing.termly()` rounds halves up to the kobo). One plan
is the default (partial unique index); a school can be moved to another
(`schools.plan_id`). The migration seeds "Standard" with placeholder prices for
the owner to set. The gateway percentage (`PLATFORM_FEE_BP`) still comes off
each parent payment through the split.

**Subscription billing (`billing.py`).** "Bill this term"
(`POST /platform/billing/generate`, with a dry run) bills every **active**
school for its current term, in a fixed number of queries and one insert:

- the set-up fee on the school's first bill only;
- the licence on its first bill of each school year;
- pupils × a third of the yearly fee.

Schools already billed for their term are skipped. A partial unique index
(school, session, term) where not voided makes double billing impossible, so
it's safe to run again after a school sets its calendar. Schools with no
current term are listed, not billed. Invoices are numbered platform-wide with
no gaps (`core/sequences.reserve_platform`), lines and payments are
append-only (trigger), and paid amounts come from recorded payments. Each
school's proprietor is emailed its bill through the outbox
(`platform.send_subscription_invoice`).

Schools pay by transfer to the platform's own account (`PLATFORM_ACCOUNT_*`,
printed on the invoice and its PDF). A platform admin records each payment,
which can't exceed the balance or be dated in the future. An unpaid bill can
be voided with a reason, and the term billed again. Card payment for
subscriptions isn't built yet.

**Sign-up (`signup.py`).** `POST /public/signup` creates the school as
**pending** (`schools.status`), its origin `signup`, a short name made from
its name (`-2`, `-3`... if taken), and its proprietor without a password. It
then emails a "confirm your school" link (the password-link flow, purpose
`signup`). Setting the password proves the email address and makes the school
**active** (`activate_if_pending`, called from
`accounts.complete_password_link`). Pending schools can't be signed in to.

The answer is the same for an email that already has an account; that person
is emailed "you already have an account" instead. Requests are limited per IP
(`SIGNUP_RATE_PER_IP`), the terms must be accepted, a hidden honeypot field
catches bots, and `SIGNUP_ENABLED=false` closes sign-up. Unconfirmed schools
are deleted after `PENDING_SCHOOL_DAYS` (task `platform.prune_pending_schools`,
daily). The sign-up writes only the platform's audit trail (with a masked
email), so a never-confirmed school can be removed completely.

**Getting started (`onboarding.py`).** `GET /school/onboarding` lists nine
steps, each worked out from the school's own data in one query: address and
phone, current term, classes, students, this term's fees, a bursar, bank
account, students' account numbers, and the term's bills. The dashboard shows
the list until every step is done.

**The platform console (`console.py`).** The overview counts schools by status
and sign-ups, pupils enrolled at active schools, money parents paid through
SchoolPay this month and last, and subscriptions billed, received, outstanding
and overdue. The schools list shows each school's plan, pupils, proprietor,
whether payments are set up, what it owes, and its last sign-in (filter by
status). A school's page adds its staff (invited, two-factor, last sign-in),
this term's bills and collections, the plan picker, its subscription bills,
and suspend/reactivate.

**Support lookup** (`GET /platform/lookup?q=`) finds:

- a school, by name, short name or email;
- a staff member, by exact email;
- a payment, by gateway or bank reference;
- a student's account number;
- a receipt, by verification code.

Students show only as initials and admission number. Every search is recorded
in the platform audit trail (`platform.support_lookup`) and rate limited. The
platform admin sees figures for every school, not pupils' or parents' details.

## 16. Data protection: export, erasure, retention, monitoring

Module: `app/modules/privacy`. These are the NDPA 2023 duties the system carries
out itself (consent is recorded on every guardian, see section 8).

**A copy of your data** (`export_guardian`). Everything held about a parent and
their children at one school, as JSON:

- the parent's details, consent and reminder choice;
- each child's details and class history;
- bills with their lines, payments with receipt numbers, and messages sent.

It takes a fixed number of queries. Parents download it from the portal
(`GET /parent/my-data`, 5 an hour); staff who manage records can download it
for a parent who asks in person (`GET /guardians/{id}/export`). Every export is
audited (`privacy.exported`).

**Erasure** (`erase_guardian`, `erase_student`; school admins and proprietors,
with a reason). Financial records must be kept (six years), so erasure
**anonymises**:

- a parent: name becomes "Erased", phone and email are removed, the phone index
  is replaced with an unusable value (that number can't sign in any more),
  parent sessions end, and message texts are cleared;
- a pupil: name becomes "Erased pupil", date of birth and gender are removed,
  their account number is closed, and message texts are cleared.

Bills, payments and allocations stay, so the school's books still add up.
Issued receipts keep the name they were issued with: they're append-only
financial records (see docs/PRIVACY.md). A parent can't be erased while any of
their children is a current pupil, and a pupil only once they have left.
`guardians.erased_at` and `students.erased_at` record when.

**Retention** (task `privacy.apply_retention`, daily 03:45, across all
schools, in bulk statements):

- message texts older than `MESSAGE_BODY_RETENTION_DAYS` (365) are cleared;
  who, when and how are kept;
- pupils who left more than `RECORDS_RETENTION_YEARS` (6) ago are anonymised,
  with their parents when those have no other child still on record.

Each affected school gets an audit entry with the counts.

**Error monitoring** (`app/core/monitoring.py`). Sentry is used only if
`SENTRY_DSN` is set, for the API and the worker. Events are sent without
request bodies, cookies, headers, query strings or user details. Every
remaining string passes the same redaction as the logs (sensitive keys,
bearer tokens, long digit runs).

**Development routes** (`/dev/fake-gateway`, `/dev/sms`) aren't mounted at all
in production.

## 17. Backend layout and conventions

```
backend/app/
  main.py          app factory, middleware, routers (/api/v1)
  openapi.py       prints the OpenAPI schema for the frontend's generated types
  core/            config, base (declarative base), db (engine/session), tenancy,
                   errors, logging + redaction, crypto (field encryption, blind
                   indexes), ratelimit, pagination, http (client IP, middleware),
                   migrate (migration runner), outbox, email
  modules/         one package per domain: models.py, schemas.py, service.py, router.py
    accounts/      staff users, sessions, MFA, passwords, roles (permissions.py), deps.py
    audit/         audit trails
    schools/       the School (tenant), its profile, platform school management
  cli.py           operator commands (create-platform-admin, seed-demo)
  workers/         Celery app and task registry
backend/alembic/   migrations
backend/tests/     pytest suite
```

Conventions:

- **Layering:** routers handle HTTP only; services hold business logic and own
  transactions (they call `commit()`); models hold no business logic. Services
  take explicit arguments (session, current user/school) so they're testable
  without HTTP.
- **IDs** are random UUIDs, so URLs reveal nothing about other schools.
- **Money** is integer kobo in `BigInteger` columns. No floats anywhere.
- **Errors**: services raise `DomainError` subclasses (`NotFound`, `Conflict`,
  `PermissionDenied`, ...). Every error response has one shape:
  `{"error": {"code", "message", "field", "details"}}`. Unexpected exceptions
  return a generic 500; the details go to the log.
- **Logging** passes through a redaction filter that masks passwords, OTPs,
  tokens, BVNs and long digit runs (account numbers). `app/core/redaction.py`
  is the one list of sensitive keys, shared with audit and gateway payload storage.
- **Migrations** run through `python -m app.core.migrate`. It holds a Postgres
  advisory lock and uses `MIGRATION_DATABASE_URL`, a role that owns the schema.
  The API connects with a DML-only role. Migrations need no API secrets.
- **Request bodies** larger than `MAX_REQUEST_BODY_BYTES` are refused with 413.

- **No database calls inside loops.** Use one query with `IN (...)` or a join,
  a bulk insert/update, or lock a batch in one `SELECT ... FOR UPDATE SKIP LOCKED`
  and commit once.

## 18. Background jobs and the outbox

Celery runs webhook processing, reminders, bulk invoicing and message sending,
so HTTP requests stay fast. Tasks take IDs, never ORM objects. Each task opens
its own session with an explicit tenant scope. Tasks are acknowledged late, so a
crashed task is redelivered; every task must therefore be idempotent.

Code never calls Celery directly. It writes to the **transactional outbox**
(`app/core/outbox.py`):

```python
outbox.enqueue(session, "accounts.send_password_link", {"user_id": ..., "purpose": "invite"})
session.commit()   # the business change and the task commit together
```

After the commit, the rows are handed to Celery straight away (one query for
all of them). If Redis is down, the rows stay pending and the `outbox.sweep`
beat task retries them every 30 seconds with exponential backoff. After 8
failures a row is marked `failed` and logged at ERROR;
`outbox.requeue_failed()` puts it back once the cause is fixed. A rolled-back
transaction never sends its tasks. Payloads hold IDs only, so no secret sits
in the table. For example, email links are generated when the email is sent.

All tasks are declared in `app/workers/tasks.py`. Scheduled tasks (Celery beat):

| Task | Every | Does |
|---|---|---|
| `outbox.sweep` | 30 s | Hands pending outbox rows to the queue |
| `accounts.prune_sessions` | 6 h | Deletes sessions that ended more than 30 days ago |
| `payments.requeue_webhooks` | 5 min | Retries gateway events that couldn't be processed yet |
| `parents.prune` | 6 h | Deletes sign-in codes after a day and ended parent sessions after 30 days |
| `notifications.schedule_reminders` | hourly (:00) | Queues fee reminders for schools whose sending hour it is |
| `notifications.retry_unsent` | hourly (:30) | Retries messages that failed on every channel (up to 3 attempts) |
| `privacy.apply_retention` | daily 03:45 | Clears old message texts; anonymises pupils who left more than `RECORDS_RETENTION_YEARS` ago, and their parents |
| `platform.prune_pending_schools` | daily 03:15 | Deletes signed-up schools nobody confirmed within `PENDING_SCHOOL_DAYS` |

On-demand tasks: `accounts.send_password_link`, `payments.process_webhook`,
`payments.provision_virtual_accounts`, `notifications.deliver`,
`platform.send_subscription_invoice`, `platform.signup_existing_account`.

A task can be held back: `outbox.enqueue(..., not_before=when)` leaves the row
pending until then, and the sweeper hands it over once it's due. Messages use
this to wait out quiet hours.

In development `CELERY_TASK_ALWAYS_EAGER=true` runs tasks in-process without Redis.

**Email** (`app/core/email.py`): `EMAIL_BACKEND=console` logs only the subject
and recipient domain; `smtp` sends via STARTTLS; `memory` is for tests.

## 19. API

All routes are under `/api/v1`. Interactive docs are at `/api/docs` outside
production.

| Area | Endpoints |
|---|---|
| Health | `GET /health`, `GET /health/ready` |
| Sign-in | `POST /auth/login`, `/auth/login/mfa`, `/auth/refresh`, `/auth/logout`; `GET /auth/me` |
| Passwords | `POST /auth/password/change`, `/auth/password/forgot`, `/auth/password/set` |
| Two-factor | `POST /auth/mfa/setup`, `/auth/mfa/confirm`, `/auth/mfa/disable`, `/auth/mfa/recovery-codes` |
| Staff | `GET/POST /staff`; `PATCH /staff/{id}`; `POST /staff/{id}/deactivate`, `/reactivate`, `/reset-mfa`, `/resend-invite` |
| School | `GET/PATCH /school` |
| Audit | `GET /audit` |
| Platform | `GET/POST /platform/schools`; `POST /platform/schools/{id}/suspend`, `/reactivate`; `GET /platform/audit` |
| Calendar | `GET/POST /academic-sessions`; `PATCH /academic-sessions/{id}`; `GET /terms/current`; `PATCH /terms/{id}`; `POST /terms/{id}/make-current` |
| Classes | `GET/POST /class-levels`; `PUT /class-levels/order`; `PATCH /class-levels/{id}`; `POST /class-levels/{id}/arms`; `PATCH /arms/{id}` |
| Students | `GET /school/roll`; `GET/POST /students` (`missing=`, `unplaced=`); `GET/PATCH /students/{id}`; `PUT /students/{id}/class`; `POST /students/{id}/withdraw`, `/reinstate` |
| Guardians | `POST /students/{id}/guardians`; `DELETE /students/{id}/guardians/{gid}`; `POST /students/{id}/guardians/{gid}/primary`; `GET /guardians`; `PATCH /guardians/{id}` |
| Import | `GET /students/import-template`; `POST /students/import` (multipart) |
| Promotion | `POST /terms/{id}/roll-forward` |
| Fee setup | `GET/POST /fee-items`; `PATCH /fee-items/{id}`; `GET/PUT /terms/{id}/fee-schedule`; `POST /terms/{id}/fee-schedule/copy`; `GET/PUT /terms/{id}/optional-fees` |
| Discounts | `GET/POST /discounts`; `PATCH /discounts/{id}`; `GET/POST /students/{id}/discounts`; `DELETE /students/{id}/discounts/{did}` |
| Payment setup | `GET/PUT /payment-settings`; `GET /payment-settings/banks`; `POST /virtual-accounts/provision`; `GET /students/{id}/virtual-account` |
| Payments | `GET /payments`; `GET /payments/{id}`; `POST /payments/{id}/reverse`; `GET /students/{id}/credit` |
| Phone calls | `PUT /guardians/{id}/calls`; parent: `PUT /parent/preferences` (`prefers_calls`, `language`); provider callback `POST /voice/africastalking` (not in the OpenAPI schema) |
| Family links | `GET /guardians/{id}/pay-link`, `POST /guardians/{id}/pay-link/replace`; public: `GET /public/pay/{token}`, `POST /public/pay/{token}/checkout` |
| Manual payments | `GET/POST /manual-payments`; `POST /manual-payments/{id}/approve`, `/reject` |
| Card | `POST /students/{id}/payment-link`; `POST /payments/verify` (public, rate limited) |
| Parent sign-in | `POST /parent/auth/request-code`, `/parent/auth/verify`, `/parent/auth/choose-school`, `/parent/auth/refresh`, `/parent/auth/logout` |
| Parent portal | `GET /parent/home`; `GET /parent/children/{id}`; `GET /parent/invoices/{id}`; `POST /parent/children/{id}/pay`; `GET /parent/receipts/{id}/pdf` |
| Messages | `GET /messages`; `GET /message-templates`, `PUT /message-templates/{kind}`, `POST /message-templates/preview`; `GET/PUT /reminder-settings`; `PUT /guardians/{id}/reminders`; `POST /students/{id}/remind`; parents: `GET/PUT /parent/preferences` |
| Platform (platform admins) | `GET /platform/overview`; `GET /platform/schools` (search, status), `GET /platform/schools/{id}`, `PUT /platform/schools/{id}/plan`, `POST /platform/schools`, `/suspend`, `/reactivate`; `GET/POST /platform/plans`, `PUT /platform/plans/{id}`; `POST /platform/billing/generate`; `GET /platform/billing/invoices`, `GET /platform/billing/invoices/{id}`, `/pdf`, `POST .../payments`, `.../void`; `GET /platform/lookup` |
| Subscription and sign-up | `GET /subscription`, `GET /subscription/invoices/{id}/pdf`; `GET /school/onboarding`; `POST /public/signup` (public, rate limited) |
| Privacy | `GET /guardians/{id}/export`; `POST /guardians/{id}/erase`, `POST /students/{id}/erase`; parents: `GET /parent/my-data` |
| Public website | `GET /public/info` (name, contact, the default plan's prices) |
| Reports | `GET /reports/overview`; `GET /reports/debtors`; `GET /reports/collections`; `GET /reports/debtors/export`, `/reports/collections/export` (`format=xlsx|pdf`) |
| Receipts | `GET /payments/{id}/receipt`; `GET /receipts/{id}/pdf`; `GET /public/receipts/{code}` (public) |
| Webhooks | `POST /webhooks/paystack`, `/webhooks/flutterwave` (`/webhooks/fake` when enabled; not in the API docs) |
| Development | `POST /dev/fake-gateway/transfers` (simulate a parent's transfer; fake gateway only); `GET /dev/sms/latest?to=` (read the last text; `SMS_BACKEND=memory` only). Neither exists in production. |
| Invoices | `POST /terms/{id}/invoices/generate`; `GET /terms/{id}/billing-summary`; `GET/POST /invoices`; `GET /invoices/{id}`; `POST /invoices/{id}/lines`, `/void`; `POST /invoices/{id}/plan`, `/plan/cancel`; `POST /terms/{id}/class-charges`; `GET/POST /students/{id}/exemptions`, `DELETE /students/{id}/exemptions/{fee_item_id}`; `POST /students/{id}/charges`; `GET /students/{id}/account` |

Lists are paginated with `limit`/`offset` and return `{items, total, limit, offset}`.

## 20. Configuration

All settings come from the environment (`backend/.env.example` documents every
variable). The defaults are safe: `ENVIRONMENT=production`, `DEBUG=false`,
secure cookies, and an empty CORS allowlist. In production the app refuses to
start with `DEBUG`, eager tasks, wildcard CORS or in-memory rate limits.

## 21. Frontend

One SPA (`frontend/src`): `console/`, `parent/`, `public/`, and `api/`, which
holds the generated `schema.d.ts` and a typed `openapi-fetch` client. In
development Vite serves on 5175 and proxies `/api` to the backend on 8002.

- **Auth** (`src/auth/AuthContext.tsx`, `src/api/client.ts`): the access token
  lives only in memory. On page load the app calls `/auth/refresh` (the cookie
  restores the session). A 401 triggers one silent refresh and a retry; if
  that fails, the user is signed out and the react-query cache is cleared.
- **Console** (`src/console/`): sign-in (with the two-factor step), forgot
  and set password, account (password, MFA enrolment with a QR code and
  recovery codes), staff, activity log, school profile, and platform schools.
  School setup: **Calendar** (sessions and terms, make current, move students
  to the next term with a preview), **Classes** (levels in promotion order,
  arms, retire/restore), **Students** (search and filter by term, class and
  status; add; detail with class history, guardians, withdraw/reinstate) and
  **Import** (template, upload, preview with row-by-row problems, import).
  Money: **Fees** (fee items, a per-term grid of amounts per class with
  optional flags and copy-from-term, discounts), **Billing** (term summary,
  one-click generation with preview, invoice list filtered by status, class and
  search), **Invoice** detail (lines, add a charge, correct an amount, void),
  and a **Fees** panel on each student (owed/billed/paid, invoices, discounts,
  opening balance, "Add a charge" from the price list, and "Not paying this
  term" exemptions). Billing has "Add a fee to classes" (preview, untick
  pupils, apply); the Fees page sets each item's standard price. `src/lib/format.ts` converts between naira
  text and kobo with string arithmetic.
  Payments: **Payments** (received payments with where each was applied, a
  record-cash/teller form with a student search, an approval queue that hides
  Approve on your own entries, reversals), **Payment settings** (connect the
  school's bank account, issue students' account numbers), and on each
  student a **Paying in** panel (their account number, a card payment link,
  recent payments). Receipt numbers on payments download the PDF.
  **Overview** (the dashboard: term figures, a progress bar per class, money
  received per day as a plain SVG chart, payment methods, how late the debts
  are; a term picker; school admins get pupils, classes and a "Needs attention"
  list instead) and **Reports** (debtors with filters, phone links and
  paging; money received over a date range; Excel and PDF downloads through
  `src/lib/download.ts`). Money queries are re-read every time the page is
  shown (`staleTime: 0`), so the figures are never 30 seconds stale.
  Set-up pages (fees, classes, calendar, staff, school profile, payment
  settings, subscription, activity log) sit under a **Setup** menu in the top
  bar. New schools see a **Getting started** checklist on the dashboard.
  **Subscription** shows the school's plan, a term's cost at its pupil
  numbers, where to pay, and its bills (PDF).
- **Platform console** (`src/console/platform/`, platform admins): Overview,
  Schools (filters, plan, pupils, payments set-up, what each owes) and a
  school's page (figures, staff, plan picker, bills, suspend), Billing (bill
  the term with a preview, record a transfer, void, PDF), Plans (prices, with
  the termly per-pupil amount worked out), and Support lookup.
- **Public website** (`src/public/Landing.tsx` at `/`, `PrivacyNotice.tsx` at
  `/privacy`): what SchoolPay does, how it works, the default plan's prices
  (from `GET /public/info`, so they always match), and the plain-language
  privacy notice. The parent portal has "Download my data"; a student's
  page has "Export data" and "Erase" for parents, and "Erase personal
  details" for a former pupil.
- **Sign-up** (`src/public/Signup.tsx`, `/signup`, linked from the sign-in
  page): the school's details, consent, and a hidden honeypot field; then
  "check your email".
  **Messages** (log of every message with its channel or why it was skipped,
  the wording editor with placeholders and preview, the reminder schedule);
  "Send reminder" and a per-parent "Stop/Restart reminders" on the student
  page; and "send each parent a message" when generating bills.
- **Parent portal** (`src/parent/`): phone, then texted code, then (if needed)
  the school; a home page with a card per child (amount owed, their account
  number with a copy button, pay by card); and a child page (bills with lines,
  payments, receipt downloads), with a switch on the home page to stop bill
  notices and reminders. It has its own auth provider. The API client's
  "realm" (`setAuthRealm`) picks the staff or parent refresh endpoint, so the
  two never share a session.
- **Public pages:** `/verify/:code` checks a receipt (genuine, reversed or not
  genuine) from its QR code. `/pay/complete` is where the gateway returns parents after a
  card payment. It shows the outcome from `POST /payments/verify` (never from
  the URL) and polls while the payment is pending. `/pay/f/:token`
  (`src/public/FamilyPay.tsx`) is a parent's family payment page: each child's
  account number with a copy button and an amount box per child (prefilled with
  what's due now) for one card payment. Staff open a parent's link from the
  student page ("Payment link": copy or replace, `console/students/FamilyLink.tsx`).
  Navigation and routes are filtered by the user's capabilities. Users who
  must enrol in MFA can only reach their account page until they do.
- Invite and reset links put the token in the URL **fragment**
  (`/console/set-password#token=...`), so it never reaches server or proxy logs.

## 22. Testing

- **Backend:** pytest with httpx `AsyncClient` against the ASGI app. Tests run on
  a real PostgreSQL database (`TEST_DATABASE_URL`, local and named `*_test`, or the
  suite refuses to start). Each run migrates a fresh schema, so migrations and DB
  triggers are tested too. Each test runs in a rolled-back transaction.
- **Frontend:** Vitest + Testing Library for components (`src/test/fetchMock.ts`
  stubs the API by route).
- **End-to-end:** Playwright (`frontend/e2e/`) starts its own API (port 8102) and
  Vite (5185) against a throwaway database seeded by `python -m app.cli seed-demo
  --reset --bare`. Run `npm run e2e`; on Windows `PW_CHANNEL=msedge` uses the installed Edge.
- **Demo data:** `python -m app.cli seed-demo` (`app/demo.py`) creates "Demo
  Model College" with a proprietor, bursar, school admin and platform admin
  (password `demo-password-123`, MFA secret `JBSWY3DPEHPK3PXP`), the 2026/2027
  calendar (first term current), classes Primary 1 to SSS 3, 240 students in
  families (siblings share a parent, ages match their class), and first-term
  fees (tuition by class, optional bus, levies, a sibling discount). On top
  of that, `app/demo_activity.py` makes the school "lived in": last session
  (returning pupils were in the class below; Primary 1, JSS 1 and a few
  transfers are new) and its carried-over debts, a few loose ends for the
  school admin's overview (new admissions without a parent, consent or account
  number, two pupils not placed, one leaver), bus sign-ups, staff-child and scholarship
  discounts, the term's bills (due 30 September), about two months of
  payments by every method with numbered receipts, part-payers, non-payers and
  a little credit, a reversed teller, two entries awaiting approval, and the
  messages parents were sent (bill, receipts, reminders; a few parents opted
  out). Payments go through the real allocation code, so everything
  reconciles, and the whole seed is about 70 SQL statements (tested). It also
  adds three more schools (one suspended) for the platform console.
  `--bare` gives the small version the browser tests use: 60 students and no
  bills yet. With the fake gateway enabled, the school's bank account is
  connected and every student has an account number. Simulate a parent's
  transfer with `POST /api/v1/dev/fake-gateway/transfers`. The parent
  `08035000000` has two children (one paid up, one owing). The seed refuses
  to run in production.
- **Query counts:** `tests/factories.py` has a `QueryCounter`. Bulk operations
  (import, roll-forward, outbox dispatch) have tests asserting their query
  count doesn't grow with the number of rows.
- **CI** (`.github/workflows/`): lint, types, tests, migration check, lockfile
  and generated-types freshness, dependency audit, and image builds. Backend and
  frontend run as separate jobs.

## 23. Known gaps

- The product is called "ParentPay" for now (`APP_NAME` / `VITE_APP_NAME`; the code uses the codename
  `schoolpay`). It's also a UK company's brand: check the trademark before a public launch.
- Gateway virtual-account KYC requirements and messaging-provider accounts
  are still open (see OWNER_TODO.md). Plan prices are placeholders.
- Schools pay subscriptions by bank transfer, recorded by a platform admin;
  card payment of subscriptions isn't built.
- There's no terms-of-service page yet (legal text from the owner).
