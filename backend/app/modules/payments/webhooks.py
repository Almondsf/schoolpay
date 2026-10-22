"""
Gateway webhooks: verify, store, enqueue. Then process in the background.

The HTTP endpoint does as little as possible: it checks the signature, stores
the event in the `webhook_events` inbox (a repeat delivery of the same event
is ignored by a unique key), and enqueues processing through the outbox in
the same transaction. It answers 200 quickly, so the gateway doesn't retry.

Processing (`process_event`) never trusts the webhook body. It asks the
gateway to verify the transaction, then records the payment against the
student who owns the receiving virtual account (or the checkout's student).
Recording is idempotent on the gateway reference.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.db import SessionLocal
from app.core.redaction import redact
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.platform import card_payments

from . import checkout
from .gateways import GatewayCode, TransactionStatus, WebhookKind, get_gateway
from .gateways.base import parse_paid_at
from .models import IntentStatus, PaymentIntent, PaymentMethod, VirtualAccount, WebhookEvent, WebhookStatus
from .service import NewPayment, record_payment

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 10
PROCESS_TASK = "payments.process_webhook"
CHECKOUT_OUTCOMES = {
    IntentStatus.SUCCEEDED: WebhookStatus.PROCESSED,
    IntentStatus.NEEDS_REVIEW: WebhookStatus.NEEDS_REVIEW,
}


def receive(session: Session, gateway: GatewayCode, headers: dict[str, str], body: bytes) -> bool:
    """
    Store a verified webhook and queue it. Raises InvalidSignature for forgeries.
    Returns False for a repeat delivery (already stored).
    """
    event = get_gateway(gateway).parse_webhook(headers, body)
    scope_platform_wide(session)
    stmt = (
        insert(WebhookEvent)
        .values(
            id=uuid.uuid4(),
            gateway=gateway,
            event_key=event.event_key[:150],
            event_type=event.event_type[:60],
            reference=event.reference,
            receiver_account_number=event.receiver_account_number,
            provider_payment_id=event.provider_payment_id,
            claimed_amount_kobo=event.amount_kobo,
            payload=redact(event.payload),
            status=WebhookStatus.RECEIVED if event.kind == WebhookKind.CHARGE else WebhookStatus.IGNORED,
        )
        .on_conflict_do_nothing(index_elements=["gateway", "event_key"])
        .returning(WebhookEvent.id)
    )
    event_id = session.execute(stmt).scalar_one_or_none()
    if event_id is not None and event.kind == WebhookKind.CHARGE:
        outbox.enqueue(session, PROCESS_TASK, {"event_id": str(event_id)})
    session.commit()
    return event_id is not None


def _finish(
    event_id: uuid.UUID,
    status: WebhookStatus,
    note: str = "",
    school_id: uuid.UUID | None = None,
    payment_id: uuid.UUID | None = None,
) -> None:
    with SessionLocal() as session:
        scope_platform_wide(session)
        session.execute(
            update(WebhookEvent)
            .where(WebhookEvent.id == event_id)
            .values(
                status=status,
                note=note[:300],
                school_id=school_id,
                payment_id=payment_id,
                processed_at=datetime.now(UTC),
            )
        )
        session.commit()


def process_event(event_id: uuid.UUID) -> WebhookStatus:
    """Turn a stored charge event into a recorded payment. Safe to run more than once."""
    with SessionLocal() as session:
        scope_platform_wide(session)
        event = session.scalar(
            select(WebhookEvent).where(WebhookEvent.id == event_id).with_for_update(skip_locked=True)
        )
        if event is None or event.status != WebhookStatus.RECEIVED:
            return event.status if event else WebhookStatus.IGNORED
        event.attempts += 1
        gateway, reference, account_number = event.gateway, event.reference, event.receiver_account_number
        payment_id = event.provider_payment_id
        session.commit()
    if reference is None:
        _finish(event_id, WebhookStatus.IGNORED, "No transaction reference")
        return WebhookStatus.IGNORED

    # A school paying its subscription bill?
    if card_payments.is_subscription_reference(reference):
        bill = card_payments.confirm(reference, payment_id)
        status = CHECKOUT_OUTCOMES.get(bill.status, WebhookStatus.IGNORED) if bill else WebhookStatus.IGNORED
        _finish(
            event_id,
            status,
            f"Subscription checkout {bill.status.value if bill else 'unknown'}",
            school_id=bill.school_id if bill else None,
        )
        return status

    # A card checkout we started?
    with SessionLocal() as session:
        scope_platform_wide(session)
        is_checkout = session.scalar(select(PaymentIntent.id).where(PaymentIntent.reference == reference)) is not None
    if is_checkout:
        intent = checkout.confirm(reference, payment_id)
        status = CHECKOUT_OUTCOMES.get(intent.status, WebhookStatus.IGNORED) if intent else WebhookStatus.IGNORED
        _finish(
            event_id,
            status,
            f"Checkout {intent.status.value if intent else 'unknown'}",
            school_id=intent.school_id if intent else None,
        )
        return status

    # Otherwise it must be a transfer into a student's virtual account
    verification = get_gateway(gateway).verify(reference, payment_id=payment_id)  # GatewayError: the task retries
    if verification.status != TransactionStatus.SUCCESS:
        _finish(event_id, WebhookStatus.IGNORED, f"Gateway says {verification.status.value}")
        return WebhookStatus.IGNORED
    receiver = verification.receiver_account_number or account_number
    with SessionLocal() as session:
        scope_platform_wide(session)
        account = (
            session.execute(
                select(VirtualAccount.school_id, VirtualAccount.student_id).where(
                    VirtualAccount.gateway == gateway, VirtualAccount.account_number == receiver
                )
            ).first()
            if receiver
            else None
        )
    if account is None or verification.currency != "NGN" or verification.amount_kobo <= 0:
        reason = "Unknown receiving account" if account is None else f"Unexpected {verification.currency} payment"
        logger.error("Webhook %s needs review: %s", event_id, reason)
        _finish(event_id, WebhookStatus.NEEDS_REVIEW, reason)
        return WebhookStatus.NEEDS_REVIEW
    school_id, student_id = account
    with SessionLocal() as session:
        scope_to_school(session, school_id)
        recorded = record_payment(
            session,
            school_id=school_id,
            payment=NewPayment(
                student_id=student_id,
                amount_kobo=verification.amount_kobo,
                method=PaymentMethod.VIRTUAL_ACCOUNT,
                received_at=parse_paid_at(verification.paid_at),
                gateway=gateway,
                gateway_reference=reference,
                reference=reference,
            ),
        )
        session.commit()
    if recorded is None:
        _finish(event_id, WebhookStatus.IGNORED, "Already recorded", school_id=school_id)
        return WebhookStatus.IGNORED
    _finish(event_id, WebhookStatus.PROCESSED, school_id=school_id, payment_id=recorded.payment.id)
    return WebhookStatus.PROCESSED


def handle_failure(event_id: uuid.UUID, error: Exception) -> None:
    """After repeated failures, park the event for an operator instead of retrying forever."""
    with SessionLocal() as session:
        scope_platform_wide(session)
        event = session.get(WebhookEvent, event_id)
        if event is not None and event.attempts >= MAX_ATTEMPTS and event.status == WebhookStatus.RECEIVED:
            event.status, event.note = WebhookStatus.FAILED, f"{type(error).__name__}: {str(error)[:200]}"
            logger.error("Webhook %s failed permanently: %s", event_id, event.note)
        session.commit()


def requeue_stuck(older_than: timedelta = timedelta(minutes=5)) -> int:
    """Beat task: events still waiting (e.g. the gateway was down) are queued again."""
    with SessionLocal() as session:
        scope_platform_wide(session)
        stuck = list(
            session.scalars(
                select(WebhookEvent.id)
                .where(
                    WebhookEvent.status == WebhookStatus.RECEIVED,
                    WebhookEvent.attempts < MAX_ATTEMPTS,
                    WebhookEvent.received_at < datetime.now(UTC) - older_than,
                )
                .limit(200)
            )
        )
        for event_id in stuck:
            outbox.enqueue(session, PROCESS_TASK, {"event_id": str(event_id)})
        session.commit()
    return len(stuck)
