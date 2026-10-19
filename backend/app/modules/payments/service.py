"""
Recording, reading and reversing payments.

Every way money arrives (virtual account transfer, card, approved cash/teller)
ends in `record_payments()`. It writes the payment once (a gateway reference can
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
from app.modules.notifications.service import queue_receipts
from app.modules.receipts.models import Receipt
from app.modules.receipts.service import issue_receipts
from app.modules.students.models import Student

from .allocation import NewAllocation, allocate
from .gateways import GatewayCode
from .models import Allocation, Payment, PaymentKind, PaymentMethod

AUTOMATIC = Actor.system("gateway")


@dataclass(frozen=True)
class Recorded:
    payment: Payment
    allocations: list[NewAllocation]
    receipt: "Receipt"


@dataclass(frozen=True)
class NewPayment:
    """Money received for one student, before it's recorded."""

    student_id: uuid.UUID
    amount_kobo: int
    method: PaymentMethod
    received_at: datetime
    gateway: GatewayCode | None = None
    gateway_reference: str | None = None
    reference: str = ""
    note: str = ""
    recorded_by: uuid.UUID | None = None
    approved_by: uuid.UUID | None = None


def record_payments(
    session: Session,
    *,
    school_id: uuid.UUID,
    payments: list[NewPayment],
    actor: Actor = AUTOMATIC,
    meta: RequestMeta | None = None,
) -> list[Recorded]:
    """
    Record receipts, apply them oldest bill first, issue their receipts and queue the
    parents' messages, in a fixed number of statements however many there are. A
    gateway transaction already recorded (a repeated webhook, or the redirect racing
    the webhook) is skipped, so the result can be shorter than the input. The caller commits.
    """
    if any(p.amount_kobo <= 0 for p in payments):
        raise DomainError("A payment must be more than ₦0.", field="amount_kobo")
    if not payments:
        return []
    ids = [uuid.uuid4() for _ in payments]
    rows = [
        {
            "id": payment_id,
            "school_id": school_id,
            "student_id": p.student_id,
            "kind": PaymentKind.RECEIPT,
            "amount_kobo": p.amount_kobo,
            "method": p.method,
            "gateway": p.gateway,
            "gateway_reference": p.gateway_reference,
            "received_at": p.received_at,
            "reference": p.reference[:100],
            "note": p.note[:300],
            "recorded_by": p.recorded_by,
            "approved_by": p.approved_by,
        }
        for payment_id, p in zip(ids, payments, strict=True)
    ]
    stmt = insert(Payment).values(rows).on_conflict_do_nothing(index_elements=["gateway", "gateway_reference"])
    inserted = set(session.scalars(stmt.returning(Payment.id)))
    if not inserted:
        return []
    by_id = {p.id: p for p in session.scalars(select(Payment).where(Payment.id.in_(inserted)))}
    if len(by_id) != len(inserted):  # inserted a moment ago in this transaction, so only a scoping bug lands here
        raise RuntimeError("Recorded payment not visible in this session's school scope.")
    recorded = [by_id[i] for i in ids if i in inserted]  # in the order given
    allocations = allocate(session, list({p.student_id for p in recorded}))
    receipts = issue_receipts(session, school_id=school_id, payments=recorded, allocations=allocations)
    queue_receipts(session, school_id, recorded)  # parents get them on WhatsApp/SMS once this commits
    results = []
    for payment, receipt in zip(recorded, receipts, strict=True):
        mine = [a for a in allocations if a.payment_id == payment.id]
        record(
            session,
            AuditAction.PAYMENT_RECORDED,
            school_id=school_id,
            actor=actor,
            meta=meta,
            target=payment,
            changes={
                "student": payment.student_id,
                "amount_kobo": payment.amount_kobo,
                "method": payment.method,
                "gateway_reference": payment.gateway_reference,
                "applied_kobo": sum(a.amount_kobo for a in mine),
                "receipt_number": receipt.number,
            },
        )
        results.append(Recorded(payment, mine, receipt))
    return results


def record_payment(
    session: Session,
    *,
    school_id: uuid.UUID,
    payment: NewPayment,
    actor: Actor = AUTOMATIC,
    meta: RequestMeta | None = None,
) -> Recorded | None:
    """One payment (see `record_payments`). None if this gateway transaction was already recorded."""
    recorded = record_payments(session, school_id=school_id, payments=[payment], actor=actor, meta=meta)
    return recorded[0] if recorded else None


def reverse_payment(
    session: Session, ctx: StaffContext, payment_id: uuid.UUID, reason: str, *, at: datetime | None = None
) -> Payment:
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
        received_at=at or datetime.now(UTC),  # `at`: only the demo seed backdates a reversal
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
