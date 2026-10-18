"""
Card payments through the gateway's hosted checkout (no card data touches us).

1. `start_checkout()` creates a PaymentIntent and gets a checkout link.
2. The parent pays on the gateway's page.
3. The browser comes back with the reference (`confirm()` via /payments/verify)
   and the gateway sends a webhook. Both end up in `confirm()`, which asks the
   gateway what really happened and records the payment exactly once.
"""

import logging
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import DomainError
from app.core.http import RequestMeta
from app.core.repo import get_or_404
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.fees.invoicing import account_summaries
from app.modules.fees.setup_service import MAX_AMOUNT_KOBO
from app.modules.students.models import Student

from .accounts import require_payment_account
from .gateways import TransactionStatus, get_gateway
from .gateways.base import parse_paid_at
from .models import IntentStatus, PaymentIntent, PaymentMethod
from .service import record_payment

logger = logging.getLogger(__name__)


def new_reference() -> str:
    return "SP" + secrets.token_hex(10).upper()


def start_checkout(
    session: Session,
    *,
    student_id: uuid.UUID,
    amount_kobo: int | None,
    email: str,
    actor: Actor,
    meta: RequestMeta,
    created_by: uuid.UUID | None,
) -> PaymentIntent:
    """A checkout link for paying (part of) a student's fees. Amount defaults to everything owed."""
    account = require_payment_account(session)
    student = get_or_404(session, Student, student_id, label="Student", field="student_id")
    owed = account_summaries(session, [student.id])[student.id].outstanding_kobo
    amount = amount_kobo if amount_kobo is not None else owed
    if not 0 < amount <= MAX_AMOUNT_KOBO:
        raise DomainError(
            "Nothing is owed. Enter the amount to pay." if amount == 0 else "Enter a valid amount.", field="amount_kobo"
        )
    intent = PaymentIntent(
        id=uuid.uuid4(),
        reference=new_reference(),
        student_id=student.id,
        gateway=account.gateway,
        amount_kobo=amount,
        email=email,
        created_by=created_by,
    )
    session.add(intent)
    settings = get_settings()
    checkout = get_gateway(account.gateway).initialize_payment(
        reference=intent.reference,
        amount_kobo=amount,
        email=email,
        subaccount_code=account.subaccount_code,
        callback_url=f"{settings.frontend_url}{settings.payment_callback_path}",
        description=f"School fees: {student.full_name} ({student.admission_number})",
    )
    intent.authorization_url = checkout.authorization_url
    record(
        session,
        AuditAction.PAYMENT_LINK_CREATED,
        school_id=student.school_id,
        actor=actor,
        meta=meta,
        target=intent,
        changes={"student": student.id, "amount_kobo": amount, "reference": intent.reference},
    )
    session.commit()
    return intent


def confirm(reference: str) -> PaymentIntent | None:
    """
    Settle a checkout from the gateway's own record. Safe to call any number of
    times, from the redirect and the webhook at once. Returns None for an
    unknown reference.
    """
    with SessionLocal() as lookup:
        scope_platform_wide(lookup)
        school_id = lookup.scalar(select(PaymentIntent.school_id).where(PaymentIntent.reference == reference))
    if school_id is None:
        return None
    with SessionLocal() as session:
        scope_to_school(session, school_id)
        # The lock makes the redirect and the webhook take turns
        intent = session.scalar(select(PaymentIntent).where(PaymentIntent.reference == reference).with_for_update())
        if intent is None or intent.status != IntentStatus.PENDING:
            return intent
        verification = get_gateway(intent.gateway).verify(reference)
        if verification.status == TransactionStatus.PENDING:
            return intent
        if verification.status == TransactionStatus.FAILED:
            intent.status = IntentStatus.FAILED
            session.commit()
            return intent
        if verification.currency != "NGN" or verification.amount_kobo <= 0:
            intent.status = IntentStatus.NEEDS_REVIEW
            intent.review_reason = f"Paid {verification.amount_kobo} kobo in {verification.currency or '?'}"
            record(
                session,
                AuditAction.PAYMENT_NEEDS_REVIEW,
                school_id=school_id,
                actor=Actor.system("gateway"),
                target=intent,
                changes={"reference": reference, "reason": intent.review_reason},
            )
            session.commit()
            logger.warning("Checkout %s needs review: %s", reference, intent.review_reason)
            return intent
        # The verified amount is what reached the school's account, so that's what's recorded
        record_payment(
            session,
            school_id=school_id,
            student_id=intent.student_id,
            amount_kobo=verification.amount_kobo,
            method=PaymentMethod.CARD,
            received_at=parse_paid_at(verification.paid_at),
            gateway=intent.gateway,
            gateway_reference=reference,
            reference=reference,
        )
        intent.status = IntentStatus.SUCCEEDED
        session.commit()
        session.refresh(intent)
        return intent
