"""
Cash, teller, POS and direct bank transfers, recorded by staff.

Maker-checker: one person records the money and a different person approves
it. Only on approval does it become a payment and reduce what's owed. This
stops one person both taking cash and marking a debt as paid. The rule is
also a database check constraint.
"""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.clock import LAGOS
from app.core.errors import Conflict, DomainError, PermissionDenied
from app.core.repo import get_or_404
from app.modules.accounting import periods
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.fees.setup_service import MAX_AMOUNT_KOBO
from app.modules.students.models import Student

from .earmarks import Earmark, choose
from .models import MANUAL_METHODS, ManualPayment, ManualPaymentStatus, PaymentMethod
from .service import NewPayment, record_payment


def record_manual(
    session: Session,
    ctx: StaffContext,
    *,
    student_id: uuid.UUID,
    amount_kobo: int,
    method: PaymentMethod,
    received_on: date,
    reference: str,
    note: str,
    fees: list[str] | None = None,
) -> ManualPayment:
    """Record money taken by hand. `fees`: what it's for (e.g. ["WAEC"]); else tuition first, then the rest."""
    if method not in MANUAL_METHODS:
        raise DomainError("Choose cash, teller, POS or bank transfer.", field="method")
    if not 0 < amount_kobo <= MAX_AMOUNT_KOBO:
        raise DomainError("Enter an amount more than ₦0.", field="amount_kobo")
    if received_on > datetime.now(LAGOS).date():
        raise DomainError("The date can't be in the future.", field="received_on")
    if method == PaymentMethod.TELLER and not reference.strip():
        raise DomainError("Enter the teller number.", field="reference")
    periods.ensure_open(session, ctx.school_id, received_on, field="received_on")
    student = get_or_404(session, Student, student_id, label="Student", field="student_id")
    if reference.strip() and session.scalar(
        select(ManualPayment.id).where(
            ManualPayment.method == method,
            ManualPayment.reference == reference.strip(),
            ManualPayment.status != ManualPaymentStatus.REJECTED,
        )
    ):
        raise Conflict(
            f"A {method.value} payment with reference {reference.strip()} is already recorded.", field="reference"
        )
    marks = choose(session, student.id, fees, amount_kobo)[0] if fees else []
    entry = ManualPayment(
        id=uuid.uuid4(),
        earmarks=[e.to_json() for e in marks],
        student_id=student.id,
        amount_kobo=amount_kobo,
        method=method,
        received_on=received_on,
        reference=reference.strip(),
        note=note.strip(),
        recorded_by=ctx.user.id,
    )
    session.add(entry)
    record(
        session,
        AuditAction.MANUAL_PAYMENT_RECORDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=entry,
        changes={
            "student": student.id,
            "amount_kobo": amount_kobo,
            "method": method,
            "reference": entry.reference,
            "for": fees or [],
        },
    )
    session.commit()
    return entry


def _pending(session: Session, entry_id: uuid.UUID) -> ManualPayment:
    entry = get_or_404(session, ManualPayment, entry_id, label="Payment", lock=True)
    if entry.status != ManualPaymentStatus.PENDING:
        raise Conflict(f"This payment was already {entry.status.value}.")
    return entry


def approve(session: Session, ctx: StaffContext, entry_id: uuid.UUID) -> ManualPayment:
    entry = _pending(session, entry_id)
    if entry.recorded_by == ctx.user.id:
        raise PermissionDenied("Someone other than the person who recorded it must approve it.", code="maker_checker")
    periods.ensure_open(session, ctx.school_id, entry.received_on, field="received_on")
    received_at = datetime.combine(entry.received_on, time(12, 0), tzinfo=LAGOS).astimezone(UTC)
    recorded = record_payment(
        session,
        school_id=ctx.school_id,
        payment=NewPayment(
            student_id=entry.student_id,
            amount_kobo=entry.amount_kobo,
            method=entry.method,
            received_at=received_at,
            reference=entry.reference,
            note=entry.note,
            recorded_by=entry.recorded_by,
            approved_by=ctx.user.id,
            earmarks=tuple(Earmark.from_json(e) for e in entry.earmarks),
        ),
        actor=ctx.actor,
        meta=ctx.meta,
    )
    if recorded is None:  # only gateway payments can collide; manual ones never do
        raise Conflict("This payment was already recorded.")
    entry.status = ManualPaymentStatus.APPROVED
    entry.decided_by, entry.decided_at = ctx.user.id, datetime.now(UTC)
    entry.payment_id = recorded.payment.id
    record(
        session,
        AuditAction.MANUAL_PAYMENT_APPROVED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=entry,
        changes={"payment": recorded.payment.id, "amount_kobo": entry.amount_kobo},
    )
    session.commit()
    return entry


def reject(session: Session, ctx: StaffContext, entry_id: uuid.UUID, reason: str) -> ManualPayment:
    entry = _pending(session, entry_id)
    if entry.recorded_by == ctx.user.id:
        raise PermissionDenied("Someone other than the person who recorded it must decide on it.", code="maker_checker")
    entry.status = ManualPaymentStatus.REJECTED
    entry.decided_by, entry.decided_at = ctx.user.id, datetime.now(UTC)
    entry.rejection_reason = reason.strip()
    record(
        session,
        AuditAction.MANUAL_PAYMENT_REJECTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=entry,
        changes={"reason": reason, "amount_kobo": entry.amount_kobo},
    )
    session.commit()
    return entry


def manual_query(status: ManualPaymentStatus | None) -> Select[ManualPayment, Student]:
    stmt = (
        select(ManualPayment, Student)
        .join(Student, Student.id == ManualPayment.student_id)
        .order_by(ManualPayment.created_at.desc())
    )
    return stmt.where(ManualPayment.status == status) if status else stmt
