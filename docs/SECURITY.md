# Security controls

This page lists the controls in place and what's still to do before go-live.
It is updated with every change that affects security.

## Identity and access
- No public sign-up. Platform admins create schools and their first proprietor;
  proprietors invite staff by email. People choose their own passwords
  (argon2-hashed; 10+ characters, not common, not their name or email).
- Role-based **capabilities**: every endpoint checks a capability server-side,
  never a role name. Nobody can change their own role or deactivate themselves,
  a school always keeps an active proprietor, and only the three school roles
  can be assigned (no escalation to platform admin).
- **Two-factor sign-in (TOTP)** is required for proprietors, bursars and platform
  admins (`ENFORCE_MFA`). Codes can't be replayed (the last used time step is
  stored and updated atomically). Recovery codes are single-use and stored
  hashed. The TOTP secret is encrypted at rest. Enrolling needs the password;
  turning MFA off needs the password and a code.
- **Sessions are server-side.** Access tokens (15 min) carry a session id that
  is checked on every request, so sign-out, deactivation, role change,
  password change/reset, MFA reset and school suspension take effect
  immediately. Refresh tokens are httpOnly, `Secure`, `SameSite=Strict` cookies
  scoped to `/api/v1/auth`. They rotate on every use, and a reused (stolen)
  refresh token revokes its session.
- Tokens are typed (`access`, `mfa`, `password`), so an MFA ticket or reset link
  can't be used as an access token. Reset/invite links are bound to the current
  password hash, so they work once. The token sits in the URL fragment, so it
  never reaches server logs.
- Failed sign-ins look identical whether or not the email exists, and take
  the same time (a dummy hash is checked). "Forgot password" always answers 202.
- **Rate limits**: sign-in per IP and per account, the MFA step per ticket,
  password reset per IP and per email, refresh per IP, MFA changes and invites
  per user/school. Limits use shared Redis storage in production.

## Tenant isolation
- Every school-owned table has a non-null `school_id`. Scoping is applied
  automatically to every ORM query in a school-scoped session, and unscoped
  sessions that touch tenant data fail closed (`app/core/tenancy.py`).
- `school_id` on new rows comes from the session's scope, never from request
  bodies, and can't be changed afterwards.
- Primary keys are random UUIDs, so IDs in URLs can't be guessed or enumerated.
- Staff users aren't a tenant table (sign-in finds them before the school is
  known), so the staff service pins `school_id` explicitly. Tests prove a school
  can't list, view, edit, deactivate or reset another school's staff by ID.
- Platform admins have no school capabilities, and school staff have no platform ones.

## Platform
- Safe defaults: `ENVIRONMENT=production`, `DEBUG=false`, empty CORS allowlist.
  In production the app refuses to start with debug on, eager background tasks,
  wildcard CORS or per-process (in-memory) rate limits.
- Security headers on every API response: `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, a deny-all CSP, `Cache-Control: no-store`,
  and HSTS in production. nginx adds a CSP for the web app.
- Request bodies over `MAX_REQUEST_BODY_BYTES` (10 MB by default) get a 413,
  including chunked uploads without a Content-Length.
- Client IPs come from `X-Forwarded-For` only for the `NUM_PROXIES` hops we run
  behind, so they can't be spoofed.
- Errors never echo submitted values or internals. Unexpected exceptions return
  a generic 500.
- API docs (`/api/docs`) are not served in production.

## Data
- Sensitive fields are encrypted at rest with Fernet (`FIELD_ENCRYPTION_KEYS`,
  rotatable by prepending a new key). Lookups on encrypted values use a keyed
  HMAC **blind index** derived from `SECRET_KEY`. Rotating `SECRET_KEY` means
  recomputing blind indexes as well as signing everyone out.
- Logs pass through a redaction filter that masks passwords, OTPs, tokens,
  BVNs and account/card numbers. One list of sensitive keys
  (`app/core/redaction.py`) covers logs, audit entries and stored gateway payloads.

## Audit
- Sign-ins (including failures), session reuse, password/MFA changes, staff and
  school changes are recorded with actor, IP, user agent and before/after values.
  The audit entry commits in the same transaction as the change.
- The audit tables are append-only **in the database**: triggers refuse UPDATE,
  DELETE and TRUNCATE.

## Database
- Migrations run as the schema owner (`MIGRATION_DATABASE_URL`) under an
  advisory lock. The API and workers connect as a DML-only role
  (`schoolpay_app`, see `deploy/postgres-init.sh`), so a compromised API can't
  alter the schema.

## Supply chain
- Python dependencies are locked with hashes (`uv.lock`, `requirements.lock`);
  images install only the lock. CI fails if the lock is stale and runs
  `pip-audit`; the frontend runs `npm audit`.
- Images run as a non-root user (UID 10001) with a read-only filesystem in Compose.

## Background tasks
- Tasks go through a transactional outbox: a rolled-back request never sends
  its email, and a broker outage delays tasks instead of losing them.
  Outbox payloads hold IDs only; links and tokens are generated at send time.

## Before go-live
- Independent penetration test of the deployed system.
- Back up the encryption keys separately from database backups, and test a restore.
- Create the first platform admin with `python -m app.cli create-platform-admin`
  and deliver the printed link privately. Never run `seed-demo` against
  production (it refuses to).
