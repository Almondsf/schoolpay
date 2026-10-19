"""
Celery application. Webhook processing, reminders, bulk invoicing and message
sending run here so HTTP requests stay fast.

Run a worker:  celery -A app.workers.celery_app worker -l info
Run the scheduler:  celery -A app.workers.celery_app beat -l info

Tasks take IDs, never ORM objects, and open their own session with an
explicit tenant scope (see app.workers.tasks).
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("schoolpay", broker=settings.redis_url, backend=None, include=["app.workers.tasks"])
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_acks_late=True,  # a task that crashes mid-way is redelivered; tasks must be idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    timezone="Africa/Lagos",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    beat_schedule={
        # Hands outbox rows to the queue if the immediate dispatch after commit failed
        "outbox-sweep": {"task": "outbox.sweep", "schedule": 30.0},
        "prune-sessions": {"task": "accounts.prune_sessions", "schedule": 6 * 3600.0},
        # Gateway webhooks that couldn't be processed yet (gateway down) are tried again
        "requeue-webhooks": {"task": "payments.requeue_webhooks", "schedule": 300.0},
        "prune-parent-records": {"task": "parents.prune", "schedule": 6 * 3600.0},
        # On the hour: each school's reminders go out at its chosen hour (Lagos time)
        "schedule-reminders": {"task": "notifications.schedule_reminders", "schedule": crontab(minute=0)},
        "retry-unsent-messages": {"task": "notifications.retry_unsent", "schedule": crontab(minute=30)},
        # Signed-up schools nobody confirmed are removed (they hold only the sign-up form's details)
        "prune-pending-schools": {"task": "platform.prune_pending_schools", "schedule": crontab(minute=15, hour=3)},
    },
)
