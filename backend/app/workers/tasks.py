"""
Celery task registry: every background task is declared here, wrapping a
plain function from its module (which stays testable without Celery).
Tasks must be idempotent: the outbox and late acknowledgement both mean a
task can run more than once.
"""

import uuid

from app.core import outbox
from app.core.db import SessionLocal
from app.core.tenancy import scope_to_school
from app.modules.accounts import tasks as accounts_tasks
from app.modules.payments import accounts as payment_accounts
from app.modules.payments import webhooks
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


@celery_app.task(name="payments.process_webhook")
def process_webhook(event_id: str) -> str:
    """
    Record the payment behind a gateway webhook. If the gateway can't be reached,
    the event stays 'received' and `payments.requeue_webhooks` tries again later.
    """
    try:
        return webhooks.process_event(uuid.UUID(event_id)).value
    except Exception as exc:
        webhooks.handle_failure(uuid.UUID(event_id), exc)
        raise


@celery_app.task(name="payments.requeue_webhooks")
def requeue_webhooks() -> int:
    return webhooks.requeue_stuck()


@celery_app.task(name="payments.provision_virtual_accounts")
def provision_virtual_accounts(school_id: str, student_ids: list[str]) -> int:
    with SessionLocal() as session:
        scope_to_school(session, uuid.UUID(school_id))
        result = payment_accounts.provision_virtual_accounts(
            session, uuid.UUID(school_id), [uuid.UUID(s) for s in student_ids]
        )
    return result.created
