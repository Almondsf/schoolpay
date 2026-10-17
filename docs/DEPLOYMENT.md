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

## Background workers

Run exactly one `beat` and as many `worker`s as needed. Beat hands stuck outbox
rows to the queue every 30 seconds. If Redis is unavailable, emails and other
tasks wait in the `outbox_tasks` table and go out when it's back. Rows that fail
8 times are marked `failed` and logged at ERROR. After fixing the cause, requeue them:

```bash
docker compose run --rm backend python -c "from app.core import outbox; print(outbox.requeue_failed())"
```

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
