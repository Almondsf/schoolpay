"""
Problems to fix: everything stuck across all schools, on one page for the platform admin.

- **Payment notifications** the worker parked: needs_review (money arrived but couldn't be matched
  safely, e.g. an unknown account number) or failed (kept failing). Retry puts one back in the
  queue after the cause is fixed; resolve closes it with a note (e.g. "refunded by Paystack").
- **Card checkouts in review**: a parent's or a school's card payment that was paid but didn't fit
  (wrong amount, bill cancelled). The money is real; the admin sorts it out with the school and
  records what was done.
- **Background tasks** the outbox gave up on (dead-lettered), with a retry.
- **Messages that failed** in the last 7 days, per school: read-only, to spot a broken provider.

Lists are capped (newest first) and every function is a fixed number of queries. Actions are
audited in the platform's trail; children's and parents' details are never shown.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.errors import Conflict, NotFound
from app.core.outbox import OutboxStatus, OutboxTask
from app.modules.accounts.deps import Principal
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.notifications.models import MessageLog, MessageStatus
from app.modules.payments.gateways import GatewayCode
from app.modules.payments.models import IntentStatus, PaymentIntent, WebhookEvent, WebhookStatus
from app.modules.payments.webhooks import PROCESS_TASK
from app.modules.schools.models import School

from .models import SubscriptionCheckout

LIMIT = 100
MESSAGE_DAYS = 7


@dataclass(frozen=True)
class WebhookProblem:
    id: uuid.UUID
    gateway: GatewayCode
    status: WebhookStatus
    event_type: str
    reference: str | None
    school_name: str | None
    claimed_amount_kobo: int | None
    note: str
    attempts: int
    received_at: datetime


@dataclass(frozen=True)
class CheckoutProblem:
    kind: str  # "school_fees" | "subscription"
    reference: str
    school_name: str
    amount_kobo: int
    review_reason: str
    created_at: datetime


@dataclass(frozen=True)
class TaskProblem:
    id: uuid.UUID
    task_name: str
    attempts: int
    last_error: str
    created_at: datetime


@dataclass(frozen=True)
class MessageProblem:
    school_name: str
    failed: int
    last_error: str


@dataclass(frozen=True)
class Problems:
    webhooks: list[WebhookProblem]
    checkouts: list[CheckoutProblem]
    tasks: list[TaskProblem]
    messages: list[MessageProblem]

    @property
    def total(self) -> int:
        return len(self.webhooks) + len(self.checkouts) + len(self.tasks)


def problems(session: Session) -> Problems:
    """Everything stuck. Five queries. The session is platform-wide."""
    webhooks = [
        WebhookProblem(**row._asdict())
        for row in session.execute(
            select(
                WebhookEvent.id,
                WebhookEvent.gateway,
                WebhookEvent.status,
                WebhookEvent.event_type,
                WebhookEvent.reference,
                School.name.label("school_name"),
                WebhookEvent.claimed_amount_kobo,
                WebhookEvent.note,
                WebhookEvent.attempts,
                WebhookEvent.received_at,
            )
            .outerjoin(School, School.id == WebhookEvent.school_id)
            .where(WebhookEvent.status.in_([WebhookStatus.NEEDS_REVIEW, WebhookStatus.FAILED]))
            .order_by(WebhookEvent.received_at.desc())
            .limit(LIMIT)
        ).all()
    ]
    fee_checkouts = session.execute(
        select(
            PaymentIntent.reference,
            School.name,
            PaymentIntent.amount_kobo,
            PaymentIntent.review_reason,
            PaymentIntent.created_at,
        )
        .join(School, School.id == PaymentIntent.school_id)
        .where(PaymentIntent.status == IntentStatus.NEEDS_REVIEW, PaymentIntent.resolved_at.is_(None))
        .order_by(PaymentIntent.created_at.desc())
        .limit(LIMIT)
    ).all()
    sub_checkouts = session.execute(
        select(
            SubscriptionCheckout.reference,
            School.name,
            SubscriptionCheckout.amount_kobo,
            SubscriptionCheckout.review_reason,
            SubscriptionCheckout.created_at,
        )
        .join(School, School.id == SubscriptionCheckout.school_id)
        .where(SubscriptionCheckout.status == IntentStatus.NEEDS_REVIEW, SubscriptionCheckout.resolved_at.is_(None))
        .order_by(SubscriptionCheckout.created_at.desc())
        .limit(LIMIT)
    ).all()
    checkouts = sorted(
        [CheckoutProblem("school_fees", *r) for r in fee_checkouts]
        + [CheckoutProblem("subscription", *r) for r in sub_checkouts],
        key=lambda c: c.created_at,
        reverse=True,
    )
    tasks = [
        TaskProblem(*row)
        for row in session.execute(
            select(
                OutboxTask.id, OutboxTask.task_name, OutboxTask.attempts, OutboxTask.last_error, OutboxTask.created_at
            )
            .where(OutboxTask.status == OutboxStatus.FAILED)
            .order_by(OutboxTask.created_at.desc())
            .limit(LIMIT)
        ).all()
    ]
    since = datetime.now(UTC) - timedelta(days=MESSAGE_DAYS)
    messages = [
        MessageProblem(name, int(failed), last_error or "")
        for name, failed, last_error in session.execute(
            select(School.name, func.count(), func.max(MessageLog.error))
            .join(School, School.id == MessageLog.school_id)
            .where(MessageLog.status == MessageStatus.FAILED, MessageLog.created_at >= since)
            .group_by(School.name)
            .order_by(func.count().desc())
            .limit(LIMIT)
        ).all()
    ]
    return Problems(webhooks, checkouts, tasks, messages)


def count(session: Session) -> int:
    """How many problems need a person (for the overview). One query."""
    webhooks = (
        select(func.count())
        .where(WebhookEvent.status.in_([WebhookStatus.NEEDS_REVIEW, WebhookStatus.FAILED]))
        .scalar_subquery()
    )
    fees = (
        select(func.count())
        .where(PaymentIntent.status == IntentStatus.NEEDS_REVIEW, PaymentIntent.resolved_at.is_(None))
        .scalar_subquery()
    )
    subs = (
        select(func.count())
        .where(SubscriptionCheckout.status == IntentStatus.NEEDS_REVIEW, SubscriptionCheckout.resolved_at.is_(None))
        .scalar_subquery()
    )
    tasks = select(func.count()).where(OutboxTask.status == OutboxStatus.FAILED).scalar_subquery()
    return int(session.scalar(select(webhooks + fees + subs + tasks)) or 0)


# --- Actions -----------------------------------------------------------------------------------------


def _parked_webhook(session: Session, event_id: uuid.UUID) -> WebhookEvent:
    event = session.scalar(select(WebhookEvent).where(WebhookEvent.id == event_id).with_for_update())
    if event is None:
        raise NotFound("Notification not found.")
    if event.status not in (WebhookStatus.NEEDS_REVIEW, WebhookStatus.FAILED):
        raise Conflict("This notification isn't waiting for anyone.")
    return event


def retry_webhook(session: Session, admin: Principal, event_id: uuid.UUID) -> None:
    """Process a parked notification again (after the cause is fixed). Recording stays idempotent."""
    event = _parked_webhook(session, event_id)
    before = event.status
    event.status, event.attempts, event.note = WebhookStatus.RECEIVED, 0, f"Retried by {admin.user.email}"
    outbox.enqueue(session, PROCESS_TASK, {"event_id": str(event.id)})
    record(
        session,
        AuditAction.PROBLEM_RETRIED,
        school_id=None,
        actor=admin.actor,
        meta=admin.meta,
        target=event,
        changes={"what": "webhook", "reference": event.reference, "was": before},
    )
    session.commit()


def resolve_webhook(session: Session, admin: Principal, event_id: uuid.UUID, note: str) -> None:
    """Close a parked notification that was dealt with by hand (e.g. refunded, or recorded as cash)."""
    event = _parked_webhook(session, event_id)
    before = event.status
    event.status, event.note = WebhookStatus.IGNORED, f"Resolved: {note.strip()}"[:300]
    event.processed_at = datetime.now(UTC)
    for school in {None, event.school_id}:
        record(
            session,
            AuditAction.PROBLEM_RESOLVED,
            school_id=school,
            actor=admin.actor,
            meta=admin.meta,
            target=event,
            changes={"what": "webhook", "reference": event.reference, "was": before, "note": note.strip()[:300]},
        )
    session.commit()


def resolve_checkout(session: Session, admin: Principal, reference: str, note: str) -> None:
    """Record how a card payment in review was sorted out. Its money record isn't touched."""
    row: PaymentIntent | SubscriptionCheckout | None
    if reference.startswith("WS"):  # a school paying its subscription
        row = session.scalar(
            select(SubscriptionCheckout).where(SubscriptionCheckout.reference == reference).with_for_update()
        )
    else:
        row = session.scalar(select(PaymentIntent).where(PaymentIntent.reference == reference).with_for_update())
    if row is None:
        raise NotFound("Payment not found.")
    if row.status != IntentStatus.NEEDS_REVIEW or row.resolved_at is not None:
        raise Conflict("This payment isn't waiting for review.")
    row.resolved_at, row.resolved_by, row.resolution_note = datetime.now(UTC), admin.user.id, note.strip()[:300]
    for school in (None, row.school_id):
        record(
            session,
            AuditAction.PROBLEM_RESOLVED,
            school_id=school,
            actor=admin.actor,
            meta=admin.meta,
            target=row,
            changes={"what": "checkout", "reference": reference, "note": note.strip()[:300]},
        )
    session.commit()


def retry_task(session: Session, admin: Principal, task_id: uuid.UUID) -> None:
    """Put one dead-lettered background task back in the queue."""
    done = session.execute(
        update(OutboxTask)
        .where(OutboxTask.id == task_id, OutboxTask.status == OutboxStatus.FAILED)
        .values(status=OutboxStatus.PENDING, attempts=0, next_attempt_at=func.now())
        .returning(OutboxTask.task_name)
    ).scalar_one_or_none()
    if done is None:
        raise NotFound("Task not found, or it isn't failed.")
    record(
        session,
        AuditAction.PROBLEM_RETRIED,
        school_id=None,
        actor=admin.actor,
        meta=admin.meta,
        changes={"what": "task", "task": done, "id": str(task_id)},
    )
    session.commit()
