#!/bin/sh
# One container runs everything: migrations, the background worker (with its scheduler) and the API.
# For hosts without separate worker services, e.g. Render's free plan (Docker Command:
# `sh bin/start-all-in-one.sh`). With separate services, run each part on its own instead (DEPLOYMENT.md).
set -e
python -m app.core.migrate
# The scheduler keeps its state in /tmp: the app's own folders aren't writable by the app user
celery -A app.workers.celery_app worker -B -l info --concurrency 1 --schedule /tmp/celerybeat-schedule &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8002}" --workers 1 --proxy-headers --forwarded-allow-ips '*'
