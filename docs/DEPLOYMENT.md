# Deployment

## Components

| Service | Image | Notes |
|---|---|---|
| `db` | postgres:18 | Data. Back it up (see below). |
| `redis` | redis:7.4 | Celery broker and shared rate-limit counters |
| `migrate` | backend | One-shot: `python -m app.core.migrate` as the schema owner |
| `backend` | backend | API (uvicorn), DML-only database role |
| `worker` | backend | Celery worker |
| `beat` | backend | Celery scheduler (run exactly one) |
| `frontend` | frontend | nginx: serves the SPA and proxies `/api` to `backend` |

## With Docker Compose

```bash
cp .env.example .env          # POSTGRES_PASSWORD, APP_DB_PASSWORD, SECRET_KEY, FIELD_ENCRYPTION_KEYS
docker compose up -d --build
curl http://localhost:8080/api/v1/health
```

Compose binds the web app to `127.0.0.1:8080`. Put a TLS-terminating reverse
proxy (Caddy, nginx, a cloud load balancer) in front of it and set
`FRONTEND_URL` to the public HTTPS URL. With one proxy in front of the
frontend container, `NUM_PROXIES` should be the total count of proxies that
add `X-Forwarded-For` (2 in that case).

## First platform admin

```bash
docker compose run --rm backend python -m app.cli create-platform-admin --email you@example.com --name "Your Name"
```

It prints a one-time link to set a password (there's nobody to email it from
yet). Send it privately. The admin must turn on two-factor sign-in at first sign-in.

## Email

Set `EMAIL_BACKEND=smtp` with `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD` and `EMAIL_FROM`; invites and password resets go out through it.
With `console` they're only logged (subject and recipient domain).

## Platform billing and sign-up

Set `PLATFORM_BANK_NAME`, `PLATFORM_ACCOUNT_NUMBER` and `PLATFORM_ACCOUNT_NAME` to the platform's own bank account: it's printed on every subscription invoice and email. `SUBSCRIPTION_DUE_DAYS` (30) sets the payment terms. Set the plan prices in the console (Plans) before the first billing run; the seeded "Standard" plan has placeholder prices.

Self sign-up is on by default (`SIGNUP_ENABLED`), limited by `SIGNUP_RATE_PER_IP`. Beat must run for unconfirmed sign-ups to be cleaned up (`PENDING_SCHOOL_DAYS`).

## Messages to parents

Messages go by **WhatsApp first**, then SMS, then email, whichever works.

**SMS (Termii).** `SMS_BACKEND=termii`, `TERMII_API_KEY`, and a sender ID
registered with Termii (`TERMII_SENDER_ID`). Parent sign-in codes also need
SMS, so the parent portal doesn't work without it.

**WhatsApp (Meta Cloud API).**
1. In Meta Business Manager, create a WhatsApp Business account and a phone
   number, and a permanent (system-user) access token with
   `whatsapp_business_messaging`.
2. Set `WHATSAPP_BACKEND=cloud`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`
   (and `WHATSAPP_LANGUAGE`, default `en`).
3. In WhatsApp Manager, create three **Utility** templates with body
   parameters in exactly this order (names can differ; set them in
   `WHATSAPP_TEMPLATES=receipt=…,fee_notice=…,reminder=…`):

| Template (default name) | Parameters `{{1}}`, `{{2}}`, … |
|---|---|
| `payment_receipt` | parent name, amount, child's name, school name, receipt number, balance, verify link |
| `fee_notice` | parent name, child's name, term, school name, amount, how to pay, the parent's family payment link |
| `fee_reminder` | parent name, school name, balance, child's name, how to pay, the parent's family payment link |

   For example `payment_receipt`: "Dear {{1}}, we have received {{2}} for {{3}}
   at {{4}}. Receipt no. {{5}}. Outstanding balance: {{6}}. Check this receipt
   at {{7}}". Messages fail over to SMS until the templates are approved.
4. With `WHATSAPP_BACKEND=off`, messages go by SMS and email only.

Messages queued longer than `MESSAGE_MAX_AGE_HOURS` (24) are skipped rather
than sent late. Bill notices and reminders are never sent between
`QUIET_HOURS_START` and `QUIET_HOURS_END` (20:00–07:00 Lagos); they wait for
the morning. Beat must be running for automatic reminders.

## Background workers

Run exactly one `beat` and as many `worker`s as needed. Beat hands stuck outbox
rows to the queue every 30 seconds. If Redis is unavailable, emails and other
tasks wait in the `outbox_tasks` table and go out when it's back. Rows that fail
8 times are marked `failed` and logged at ERROR. After fixing the cause, requeue them:

```bash
docker compose run --rm backend python -c "from app.core import outbox; print(outbox.requeue_failed())"
```

## Payment gateways

1. Set `PAYMENT_GATEWAYS` and the keys (`PAYSTACK_SECRET_KEY`;
   `FLUTTERWAVE_SECRET_KEY` and `FLUTTERWAVE_WEBHOOK_HASH`). The app refuses to
   start in production with a gateway enabled but its keys missing, or with
   the fake gateway enabled.
2. In each gateway's dashboard, set the webhook URL:
   - Paystack: `https://<your-domain>/api/v1/webhooks/paystack`
   - Flutterwave: `https://<your-domain>/api/v1/webhooks/flutterwave` (and set the secret hash)
3. Confirm with Paystack that dedicated virtual accounts are enabled for the
   business and which bank to issue them from (`PAYSTACK_PREFERRED_BANK`).
   Test a transfer in the sandbox end to end before onboarding a school
   (PLAN §3.2).
4. Run the worker and beat: webhooks are processed in the background and
   retried every 5 minutes if the gateway is unreachable.

Events that couldn't be matched (`needs_review`) or kept failing (`failed`) are
in the `webhook_events` table for an operator to look at.

## Backups

Run `deploy/backup.sh` daily from the host running Compose (cron or a systemd timer). It dumps the database, encrypts it with [age](https://age-encryption.org) to `BACKUP_AGE_RECIPIENT` (a public key), writes a checksum, copies it off the server with `BACKUP_UPLOAD_CMD` (e.g. rclone to object storage) and keeps `BACKUP_KEEP_DAYS` locally. Keep the age **private** key and the `FIELD_ENCRYPTION_KEYS` off the server and apart from each other: a restore needs both.

```bash
BACKUP_DIR=/var/backups/schoolpay BACKUP_AGE_RECIPIENT=age1... \
BACKUP_UPLOAD_CMD='rclone copy "$1" offsite:schoolpay-backups/' deploy/backup.sh
```

**Test a restore every month** into an empty database (`deploy/restore.sh` refuses a non-empty one), then start the API against it with production's `FIELD_ENCRYPTION_KEYS` and check `/api/v1/health/ready`, a school's billing summary and a receipt PDF.

## Monitoring

- Health: `/api/v1/health` (liveness) and `/api/v1/health/ready` (database) for the load balancer and an uptime checker.
- Errors: set `SENTRY_DSN` (and `RELEASE` to the deployed commit) for the API and worker; events are scrubbed of personal data before they're sent. `SENTRY_TRACES_SAMPLE_RATE` turns on performance tracing.
- Logs go to stdout as text with secrets redacted; ship them to your log service.
- Watch the outbox: rows marked `failed` are logged at ERROR (see Background workers).

## Data protection

Beat runs the retention job daily (`RECORDS_RETENTION_YEARS`, `MESSAGE_BODY_RETENTION_DAYS`). Set `SUPPORT_EMAIL`: it's shown on the website and privacy notice.

## Database roles

`deploy/postgres-init.sh` runs when the database volume is first created. It
creates `schoolpay_app`, which can read and write rows but can't change the
schema. Migrations run as the owner role (`MIGRATION_DATABASE_URL`). On a
managed database, create the same two roles by hand.

## Release order

1. Back up the database.
2. Run migrations (`migrate` service, or `python -m app.core.migrate` as a
   one-off job). Concurrent runs wait on an advisory lock.
3. Roll the API, worker and beat to the new image.

## Secrets

Every backend setting is documented in `backend/.env.example`. Generate:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                               # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FIELD_ENCRYPTION_KEYS
```

Store `FIELD_ENCRYPTION_KEYS` somewhere other than the database backups: losing
it makes encrypted fields unreadable, and storing it with the backups defeats
the encryption.

## Health checks

- `GET /api/v1/health`: liveness (process up)
- `GET /api/v1/health/ready`: readiness (database and Redis reachable; 503 if not)
