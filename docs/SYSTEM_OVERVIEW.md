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
8. [Backend layout and conventions](#8-backend-layout-and-conventions)
9. [Background jobs and the outbox](#9-background-jobs-and-the-outbox)
10. [API](#10-api)
11. [Configuration](#11-configuration)
12. [Frontend](#12-frontend)
13. [Testing](#13-testing)
14. [Known gaps](#14-known-gaps)

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

## 8. Backend layout and conventions

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

## 9. Background jobs and the outbox

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

In development `CELERY_TASK_ALWAYS_EAGER=true` runs tasks in-process without Redis.

**Email** (`app/core/email.py`): `EMAIL_BACKEND=console` logs only the subject
and recipient domain; `smtp` sends via STARTTLS; `memory` is for tests.

## 10. API

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

Lists are paginated with `limit`/`offset` and return `{items, total, limit, offset}`.

## 11. Configuration

All settings come from the environment (`backend/.env.example` documents every
variable). The defaults are safe: `ENVIRONMENT=production`, `DEBUG=false`,
secure cookies, and an empty CORS allowlist. In production the app refuses to
start with `DEBUG`, eager tasks, wildcard CORS or in-memory rate limits.

## 12. Frontend

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
  Navigation and routes are filtered by the user's capabilities. Users who
  must enrol in MFA can only reach their account page until they do.
- Invite and reset links put the token in the URL **fragment**
  (`/console/set-password#token=...`), so it never reaches server or proxy logs.

## 13. Testing

- **Backend:** pytest with httpx `AsyncClient` against the ASGI app. Tests run on
  a real PostgreSQL database (`TEST_DATABASE_URL`, local and named `*_test`, or the
  suite refuses to start). Each run migrates a fresh schema, so migrations and DB
  triggers are tested too. Each test runs in a rolled-back transaction.
- **Frontend:** Vitest + Testing Library for components (`src/test/fetchMock.ts`
  stubs the API by route).
- **End-to-end:** Playwright (`frontend/e2e/`) starts its own API (port 8102) and
  Vite (5185) against a throwaway database seeded by `python -m app.cli seed-demo
  --reset`. Run `npm run e2e`; on Windows `PW_CHANNEL=msedge` uses the installed Edge.
- **Demo data:** `python -m app.cli seed-demo` creates "Demo Model College"
  with a proprietor, bursar, school admin and platform admin (password
  `demo-password-123`, MFA secret `JBSWY3DPEHPK3PXP`). It refuses to run in production.
- **CI** (`.github/workflows/`): lint, types, tests, migration check, lockfile
  and generated-types freshness, dependency audit, and image builds. Backend and
  frontend run as separate jobs.

## 14. Known gaps

- The product name "SchoolPay" is a placeholder.
- Pricing model, gateway virtual-account KYC requirements and messaging-provider
  accounts are still open decisions (see PLAN.md §3).
