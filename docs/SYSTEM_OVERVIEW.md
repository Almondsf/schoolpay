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
12. [Backend layout and conventions](#12-backend-layout-and-conventions)
13. [Background jobs and the outbox](#13-background-jobs-and-the-outbox)
14. [API](#14-api)
15. [Configuration](#15-configuration)
16. [Frontend](#16-frontend)
17. [Testing](#17-testing)
18. [Known gaps](#18-known-gaps)

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
classes with no fees set. It runs **a fixed number of queries** whatever the
number of students (tested), and 60 invoices take about 0.4 s.

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

## 12. Backend layout and conventions

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

## 13. Background jobs and the outbox

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

On-demand tasks: `accounts.send_password_link`, `payments.process_webhook`,
`payments.provision_virtual_accounts`.

In development `CELERY_TASK_ALWAYS_EAGER=true` runs tasks in-process without Redis.

**Email** (`app/core/email.py`): `EMAIL_BACKEND=console` logs only the subject
and recipient domain; `smtp` sends via STARTTLS; `memory` is for tests.

## 14. API

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
| Students | `GET/POST /students`; `GET/PATCH /students/{id}`; `PUT /students/{id}/class`; `POST /students/{id}/withdraw`, `/reinstate` |
| Guardians | `POST /students/{id}/guardians`; `DELETE /students/{id}/guardians/{gid}`; `POST /students/{id}/guardians/{gid}/primary`; `GET /guardians`; `PATCH /guardians/{id}` |
| Import | `GET /students/import-template`; `POST /students/import` (multipart) |
| Promotion | `POST /terms/{id}/roll-forward` |
| Fee setup | `GET/POST /fee-items`; `PATCH /fee-items/{id}`; `GET/PUT /terms/{id}/fee-schedule`; `POST /terms/{id}/fee-schedule/copy`; `GET/PUT /terms/{id}/optional-fees` |
| Discounts | `GET/POST /discounts`; `PATCH /discounts/{id}`; `GET/POST /students/{id}/discounts`; `DELETE /students/{id}/discounts/{did}` |
| Payment setup | `GET/PUT /payment-settings`; `GET /payment-settings/banks`; `POST /virtual-accounts/provision`; `GET /students/{id}/virtual-account` |
| Payments | `GET /payments`; `GET /payments/{id}`; `POST /payments/{id}/reverse`; `GET /students/{id}/credit` |
| Manual payments | `GET/POST /manual-payments`; `POST /manual-payments/{id}/approve`, `/reject` |
| Card | `POST /students/{id}/payment-link`; `POST /payments/verify` (public, rate limited) |
| Receipts | `GET /payments/{id}/receipt`; `GET /receipts/{id}/pdf`; `GET /public/receipts/{code}` (public) |
| Webhooks | `POST /webhooks/paystack`, `/webhooks/flutterwave` (`/webhooks/fake` when enabled; not in the API docs) |
| Development | `POST /dev/fake-gateway/transfers` (simulate a parent's transfer; fake gateway only, never in production) |
| Invoices | `POST /terms/{id}/invoices/generate`; `GET /terms/{id}/billing-summary`; `GET/POST /invoices`; `GET /invoices/{id}`; `POST /invoices/{id}/lines`, `/void`; `GET /students/{id}/account` |

Lists are paginated with `limit`/`offset` and return `{items, total, limit, offset}`.

## 15. Configuration

All settings come from the environment (`backend/.env.example` documents every
variable). The defaults are safe: `ENVIRONMENT=production`, `DEBUG=false`,
secure cookies, and an empty CORS allowlist. In production the app refuses to
start with `DEBUG`, eager tasks, wildcard CORS or in-memory rate limits.

## 16. Frontend

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
  opening balance, one-off charges). `src/lib/format.ts` converts between naira
  text and kobo with string arithmetic.
  Payments: **Payments** (received payments with where each was applied, a
  record-cash/teller form with a student search, an approval queue that hides
  Approve on your own entries, reversals), **Payment settings** (connect the
  school's bank account, issue students' account numbers), and on each
  student a **Paying in** panel (their account number, a card payment link,
  recent payments). Receipt numbers on payments download the PDF.
- **Public pages:** `/verify/:code` checks a receipt (genuine, reversed or not
  genuine) from its QR code. `/pay/complete` is where the gateway returns parents after a
  card payment. It shows the outcome from `POST /payments/verify` (never from
  the URL) and polls while the payment is pending.
  Navigation and routes are filtered by the user's capabilities. Users who
  must enrol in MFA can only reach their account page until they do.
- Invite and reset links put the token in the URL **fragment**
  (`/console/set-password#token=...`), so it never reaches server or proxy logs.

## 17. Testing

- **Backend:** pytest with httpx `AsyncClient` against the ASGI app. Tests run on
  a real PostgreSQL database (`TEST_DATABASE_URL`, local and named `*_test`, or the
  suite refuses to start). Each run migrates a fresh schema, so migrations and DB
  triggers are tested too. Each test runs in a rolled-back transaction.
- **Frontend:** Vitest + Testing Library for components (`src/test/fetchMock.ts`
  stubs the API by route).
- **End-to-end:** Playwright (`frontend/e2e/`) starts its own API (port 8102) and
  Vite (5185) against a throwaway database seeded by `python -m app.cli seed-demo
  --reset`. Run `npm run e2e`; on Windows `PW_CHANNEL=msedge` uses the installed Edge.
- **Demo data:** `python -m app.cli seed-demo` (`app/demo.py`) creates "Demo
  Model College" with a proprietor, bursar, school admin and platform admin
  (password `demo-password-123`, MFA secret `JBSWY3DPEHPK3PXP`), the 2026/2027
  calendar (first term current), classes Primary 1 to SSS 3, 60 students
  with guardians (siblings share a parent), and first-term fees (tuition by
  class, optional bus, levies, a sibling discount); invoices are left for you
  to generate. With the fake gateway enabled it also connects the school's
  bank account and gives every student an account number. Simulate a parent's
  transfer with `POST /api/v1/dev/fake-gateway/transfers`. It refuses to run
  in production.
- **Query counts:** `tests/factories.py` has a `QueryCounter`. Bulk operations
  (import, roll-forward, outbox dispatch) have tests asserting their query
  count doesn't grow with the number of rows.
- **CI** (`.github/workflows/`): lint, types, tests, migration check, lockfile
  and generated-types freshness, dependency audit, and image builds. Backend and
  frontend run as separate jobs.

## 18. Known gaps

- The product name "SchoolPay" is a placeholder.
- Pricing model, gateway virtual-account KYC requirements and messaging-provider
  accounts are still open decisions (see PLAN.md §3).
