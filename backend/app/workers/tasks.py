"""
Celery task registry: every background task is declared here, wrapping a
plain function from its module (which stays testable without Celery).
Tasks must be idempotent: the outbox and late acknowledgement both mean a
task can run more than once.
"""

from app.core import outbox
from app.modules.accounts import tasks as accounts_tasks
from app.workers.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    """Liveness check for the worker."""
    return "pong"


@celery_app.task(name="outbox.sweep")
def outbox_sweep() -> int:
    return outbox.sweep()


@celery_app.task(name="accounts.send_password_link", autoretry_for=(OSError,), retry_backoff=True, max_retries=5)
def send_password_link(user_id: str, purpose: str) -> None:
    accounts_tasks.send_password_link(user_id, purpose)


@celery_app.task(name="accounts.prune_sessions")
def prune_sessions() -> int:
    return accounts_tasks.prune_sessions()
