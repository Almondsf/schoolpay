"""
Schools paying their subscription bill by card (or Paystack's bank transfer / USSD).

1. `start()`: a proprietor or bursar asks to pay a bill. We open a hosted checkout with **no
   school subaccount**, so the money settles to the platform's own gateway balance: this is
   the platform's income, not school money. The amount is what's left on the bill.
2. The gateway sends them back to `/console/subscription?reference=...`, and sends a webhook.
3. Both end in `confirm()`, which asks the gateway what really happened (never the browser
   or the webhook body) and records one `SubscriptionPayment`, unique on the gateway
   reference, so a payment is never counted twice.

A payment the bill can no longer take (paid by transfer meanwhile, or voided) or that isn't
what we asked for is parked as needs_review for a platform admin, never forced onto the bill.
"""

import logging
import secrets
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import LAGOS
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import Conflict, NotFound
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.payments.gateways import TransactionStatus, get_gateway
from app.modules.payments.gateways.base import parse_paid_at
from app.modules.payments.models import IntentStatus
from app.modules.payments.providers import active_payment_gateway

from .billing import invoice_detail
from .models import SubscriptionCheckout, SubscriptionInvoice, SubscriptionPayment

logger = logging.getLogger(__name__)
PREFIX = "WS"  # school fee checkouts start "SP"; the prefix tells the webhook which kind it is
RETURN_PATH = "/console/subscription"


def new_reference() -> str:
    return PREFIX + secrets.token_hex(10).upper()


def is_subscription_reference(reference: str) -> bool:
    return reference.startswith(PREFIX)


def start(session: Session, ctx: StaffContext, invoice_id: uuid.UUID) -> SubscriptionCheckout:
    """A checkout for what's left on one of the school's bills. The session is scoped to the school."""
    invoice = session.scalar(
        select(SubscriptionInvoice).where(SubscriptionInvoice.id == invoice_id).with_for_update()
    )  # scoped: another school's bill is "not found"
    if invoice is None:
        raise NotFound("Invoice not found.")
    if invoice.voided_at is not None:
        raise Conflict("This bill was cancelled.")
    row, _, _ = invoice_detail(session, invoice.id)
    if row.balance_kobo <= 0:
        raise Conflict("This bill is already paid.")
    gateway = active_payment_gateway(session)
    settings = get_settings()
    checkout = SubscriptionCheckout(
        id=uuid.uuid4(),
        school_id=invoice.school_id,
        invoice_id=invoice.id,
        reference=new_reference(),
        gateway=gateway,
        amount_kobo=row.balance_kobo,
        email=ctx.user.email,
        created_by=ctx.user.id,
    )
    session.add(checkout)
    result = get_gateway(gateway).initialize_payment(
        reference=checkout.reference,
        amount_kobo=checkout.amount_kobo,
        email=ctx.user.email,
        callback_url=f"{settings.frontend_url}{RETURN_PATH}",
        subaccount_code=None,  # the platform's own balance
        description=f"{settings.app_name} invoice {invoice.number} ({row.period})",
    )
    checkout.authorization_url, checkout.provider_reference = result.authorization_url, result.provider_reference
    record(
        session,
        AuditAction.SUBSCRIPTION_CHECKOUT_STARTED,
        school_id=invoice.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=checkout,
        changes={"number": invoice.number, "amount_kobo": checkout.amount_kobo, "reference": checkout.reference},
    )
    session.commit()
    return checkout


def confirm(reference: str, payment_id: str | None = None) -> SubscriptionCheckout | None:
    """
    Settle a subscription checkout from the gateway's own record. Safe to call any number of
    times, from the redirect and the webhook at once. None for an unknown reference.
    """
    with SessionLocal() as lookup:
        scope_platform_wide(lookup)
        school_id = lookup.scalar(
            select(SubscriptionCheckout.school_id).where(SubscriptionCheckout.reference == reference)
        )
    if school_id is None:
        return None
    with SessionLocal() as session:
        scope_to_school(session, school_id)
        # The lock makes the redirect and the webhook take turns
        checkout = session.scalar(
            select(SubscriptionCheckout).where(SubscriptionCheckout.reference == reference).with_for_update()
        )
        if checkout is None or checkout.status != IntentStatus.PENDING:
            return checkout
        verification = get_gateway(checkout.gateway).verify(
            reference, provider_reference=checkout.provider_reference or None, payment_id=payment_id
        )
        if verification.status == TransactionStatus.PENDING:
            return checkout
        if verification.status == TransactionStatus.FAILED:
            checkout.status = IntentStatus.FAILED
            session.commit()
            return checkout
        invoice = session.scalar(
            select(SubscriptionInvoice).where(SubscriptionInvoice.id == checkout.invoice_id).with_for_update()
        )
        if invoice is None:  # the foreign key keeps it; only a broken database gets here
            raise NotFound("Invoice not found.")
        row, _, _ = invoice_detail(session, invoice.id)
        problem = ""
        if verification.currency != "NGN":
            problem = f"Paid in {verification.currency or '?'}"
        elif verification.amount_kobo != checkout.amount_kobo:
            problem = f"Paid {verification.amount_kobo} kobo, expected {checkout.amount_kobo}"
        elif verification.reference != reference:
            problem = f"For another reference ({verification.reference[:40]})"
        elif invoice.voided_at is not None:
            problem = "The bill was cancelled"
        elif verification.amount_kobo > row.balance_kobo:  # e.g. a transfer was recorded meanwhile
            problem = f"Only {row.balance_kobo} kobo was left on the bill"
        if problem:
            checkout.status, checkout.review_reason = IntentStatus.NEEDS_REVIEW, problem
            for school in (None, school_id):
                record(
                    session,
                    AuditAction.SUBSCRIPTION_CHECKOUT_NEEDS_REVIEW,
                    school_id=school,
                    actor=Actor.system("gateway"),
                    target=checkout,
                    changes={"reference": reference, "reason": problem},
                )
            session.commit()
            logger.warning("Subscription checkout %s needs review: %s", reference, problem)
            return checkout
        received_on = parse_paid_at(verification.paid_at).astimezone(LAGOS).date()
        inserted = session.execute(
            insert(SubscriptionPayment)
            .values(
                id=uuid.uuid4(),
                school_id=school_id,
                invoice_id=invoice.id,
                amount_kobo=verification.amount_kobo,
                received_on=min(received_on, datetime.now(LAGOS).date()),
                reference=reference,
                gateway=checkout.gateway,
                gateway_reference=reference,
            )
            .on_conflict_do_nothing(index_elements=["gateway_reference"])
            .returning(SubscriptionPayment.id)
        ).scalar_one_or_none()
        checkout.status = IntentStatus.SUCCEEDED
        if inserted is not None:
            for school in (None, school_id):
                record(
                    session,
                    AuditAction.SUBSCRIPTION_PAYMENT_RECORDED,
                    school_id=school,
                    actor=Actor.system("gateway"),
                    target=invoice,
                    changes={"number": invoice.number, "amount_kobo": verification.amount_kobo, "reference": reference},
                )
        session.commit()
        session.refresh(checkout)
        return checkout
