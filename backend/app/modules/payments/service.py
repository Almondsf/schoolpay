"""
Recording, reading and reversing payments.

Every way money arrives (virtual account transfer, card, approved cash/teller)
ends in `record_payment()`. It writes the payment once (a gateway reference can
only ever be recorded once, however many times the webhook arrives), applies
it to invoices oldest first, and audits it, all in the caller's transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.core.http import RequestMeta
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.fees.models import Invoice
from app.modules.students.models import Student

from .allocation import NewAllocation, allocate
from .gateways import GatewayCode
from .models import Allocation, Payment, PaymentKind, PaymentMethod

AUTOMATIC = Actor.system("gateway")


@dataclass(frozen=True)
class Recorded:
    payment: Payment
    allocations: list[NewAllocation]


def record_payment(
    session: Session,
    *,
    school_id: uuid.UUID,
    student_id: uuid.UUID,
    amount_kobo: int,
    method: PaymentMethod,
    received_at: datetime,
    gateway: GatewayCode | None = None,
    gateway_reference: str | None = None,
    reference: str = "",
    note: str = "",
    recorded_by: uuid.UUID | None = None,
    approved_by: uuid.UUID | None = None,
    actor: Actor = AUTOMATIC,
    meta: RequestMeta | None = None,
) -> Recorded | None:
    """
    Record a receipt and apply it. Returns None if this gateway transaction was
    already recorded (a repeated webhook or a redirect racing the webhook).
    The caller commits.
    """
    if amount_kobo <= 0:
        raise DomainError("A payment must be more than ₦0.", field="amount_kobo")
    payment_id = uuid.uuid4()
    stmt = insert(Payment).values(
        id=payment_id,
        school_id=school_id,
        student_id=student_id,
        kind=PaymentKind.RECEIPT,
        amount_kobo=amount_kobo,
        method=method,
        gateway=gateway,
        gateway_reference=gateway_reference,
        received_at=received_at,
        reference=reference[:100],
        note=note[:300],
        recorded_by=recorded_by,
        approved_by=approved_by,
    )
    if gateway_reference is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["gateway", "gateway_reference"])
    if session.execute(stmt.returning(Payment.id)).scalar_one_or_none() is None:
        return None
    payment = session.get(Payment, payment_id)
    if payment is None:  # inserted a moment ago in this transaction, so only a scoping bug lands here
        raise RuntimeError("Recorded payment not visible in this session's school scope.")
    allocations = allocate(session, [student_id])
    record(
        session,
        AuditAction.PAYMENT_RECORDED,
        school_id=school_id,
        actor=actor,
        meta=meta,
        target=payment,
        changes={
            "student": student_id,
            "amount_kobo": amount_kobo,
            "method": method,
            "gateway_reference": gateway_reference,
            "applied_kobo": sum(a.amount_kobo for a in allocations),
        },
    )
    return Recorded(payment, allocations)


def reverse_payment(session: Session, ctx: StaffContext, payment_id: uuid.UUID, reason: str) -> Payment:
    """
    Undo a payment recorded in error (a bounced teller, a duplicate entry). A
    reversal is a new negative payment with negative allocations; the original
    is never changed. The invoices it paid become owed again.
    """
    original = get_or_404(session, Payment, payment_id, label="Payment")
    if original.kind != PaymentKind.RECEIPT:
        raise DomainError("A reversal can't itself be reversed.")
    session.execute(select(Student.id).where(Student.id == original.student_id).with_for_update())
    if session.scalar(select(Payment.id).where(Payment.reverses_payment_id == original.id)) is not None:
        raise Conflict("This payment has already been reversed.")
    per_invoice = session.execute(
        select(Allocation.invoice_id, func.sum(Allocation.amount_kobo))
        .where(Allocation.payment_id == original.id)
        .group_by(Allocation.invoice_id)
    ).all()
    reversal = Payment(
        id=uuid.uuid4(),
        student_id=original.student_id,
        kind=PaymentKind.REVERSAL,
        amount_kobo=-original.amount_kobo,
        method=original.method,
        gateway=original.gateway,
        reverses_payment_id=original.id,
        received_at=datetime.now(UTC),
        note=reason.strip()[:300],
        recorded_by=ctx.user.id,
    )
    session.add(reversal)
    session.flush()
    session.add_all(
        Allocation(payment_id=reversal.id, invoice_id=invoice_id, amount_kobo=-int(amount))
        for invoice_id, amount in per_invoice
        if amount
    )
    record(
        session,
        AuditAction.PAYMENT_REVERSED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=original,
        changes={"amount_kobo": original.amount_kobo, "reason": reason, "reversal": reversal.id},
    )
    session.commit()
    return reversal


# --- Reading -----------------------------------------------------------------------------------------


def payments_query(
    *,
    student_id: uuid.UUID | None = None,
    method: PaymentMethod | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
) -> Select[Payment, Student]:
    stmt = (
        select(Payment, Student)
        .join(Student, Student.id == Payment.student_id)
        .order_by(Payment.received_at.desc(), Payment.created_at.desc())
    )
    if student_id:
        stmt = stmt.where(Payment.student_id == student_id)
    if method:
        stmt = stmt.where(Payment.method == method)
    if since:
        stmt = stmt.where(Payment.received_at >= since)
    if until:
        stmt = stmt.where(Payment.received_at < until)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            Student.first_name.ilike(pattern)
            | Student.last_name.ilike(pattern)
            | Student.admission_number.ilike(pattern)
            | Payment.reference.ilike(pattern)
            | Payment.gateway_reference.ilike(pattern)
        )
    return stmt


@dataclass(frozen=True)
class AppliedTo:
    invoice_id: uuid.UUID
    invoice_number: int
    amount_kobo: int


def applied_to(session: Session, payment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AppliedTo]]:
    """Where each payment's money went, for any number of payments, in one query."""
    result: dict[uuid.UUID, list[AppliedTo]] = {pid: [] for pid in payment_ids}
    if not payment_ids:
        return result
    for payment_id, invoice_id, number, amount in session.execute(
        select(Allocation.payment_id, Invoice.id, Invoice.number, func.sum(Allocation.amount_kobo))
        .join(Invoice, Invoice.id == Allocation.invoice_id)
        .where(Allocation.payment_id.in_(payment_ids))
        .group_by(Allocation.payment_id, Invoice.id, Invoice.number)
        .order_by(Invoice.number)
    ).all():
        result[payment_id].append(AppliedTo(invoice_id, number, int(amount)))
    return result


def reversed_ids(session: Session, payment_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not payment_ids:
        return set()
    return set(session.scalars(select(Payment.reverses_payment_id).where(Payment.reverses_payment_id.in_(payment_ids))))
