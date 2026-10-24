"""
Writing off a debt the school won't collect (a family that left owing, a hardship case).

Anyone who handles money can ask, with a reason; only the proprietor approves (owner's rule, 2026-10-24:
writing off is how stolen fees get hidden, so two staff together mustn't be able to do it). A
proprietor's own write-off takes effect at once: there's nobody above them, and it's in the audit log and
the unusual-activity list. Approval adds a `write_off` line to the bill, dated that day, so the balance,
reminders, reports and the journal (Dr bad debts, Cr receivable) all follow; nothing is edited.

A bill on a payment plan can't be written off until the plan is cancelled (the instalments would no
longer add up). The amount is checked against the balance when asked and again when approved, in case
money arrived in between.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound, PermissionDenied
from app.modules.accounts.deps import StaffContext
from app.modules.accounts.permissions import ROLE_CAPABILITIES, Capability
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.students.models import Student

from .invoicing import _live_invoice, amounts_for
from .models import Invoice, InvoiceLine, LineKind, PaymentPlan, WriteOff, WriteOffStatus


def _approves(ctx: StaffContext) -> bool:
    return Capability.DEBTS_WRITE_OFF in ROLE_CAPABILITIES.get(ctx.user.role, frozenset())


def _balance(session: Session, invoice: Invoice) -> int:
    amounts = amounts_for(session, [invoice.id])[invoice.id]
    return amounts.total_kobo - amounts.paid_kobo


def _apply(session: Session, ctx: StaffContext, write_off: WriteOff, invoice: Invoice) -> None:
    """Approve: put the write-off line on the bill. The bill is locked by the caller."""
    balance = _balance(session, invoice)
    if write_off.amount_kobo > balance:
        raise Conflict(
            f"Only ₦{balance / 100:,.2f} is still owed on this bill now (money arrived since it was asked). "
            "Reject this and ask again for the new amount.",
            code="write_off_too_much",
        )
    position = (
        session.scalar(select(func.max(InvoiceLine.position)).where(InvoiceLine.invoice_id == invoice.id)) or 0
    ) + 1
    line = InvoiceLine(
        id=uuid.uuid4(),
        invoice_id=invoice.id,
        position=position,
        kind=LineKind.WRITE_OFF,
        description="Written off",
        amount_kobo=-write_off.amount_kobo,
        created_by=ctx.user.id,
        reason=write_off.reason,
    )
    session.add(line)
    session.flush()
    write_off.status = WriteOffStatus.APPROVED
    write_off.decided_by, write_off.decided_at = ctx.user.id, datetime.now(UTC)
    write_off.line_id = line.id
    record(
        session,
        AuditAction.WRITE_OFF_APPROVED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=write_off,
        changes={"bill": invoice.number, "amount_kobo": write_off.amount_kobo, "reason": write_off.reason},
    )


def request(session: Session, ctx: StaffContext, invoice_id: uuid.UUID, *, amount_kobo: int, reason: str) -> WriteOff:
    reason = reason.strip()
    if len(reason) < 3:
        raise DomainError("Say why the school won't collect it.", field="reason")
    if amount_kobo <= 0:
        raise DomainError("Enter the amount to write off.", field="amount_kobo")
    invoice = _live_invoice(session, invoice_id)
    if session.scalar(
        select(PaymentPlan.id).where(PaymentPlan.invoice_id == invoice.id, PaymentPlan.cancelled_at.is_(None))
    ):
        raise Conflict("This bill is on a payment plan. Cancel the plan first.", code="on_plan")
    if session.scalar(
        select(WriteOff.id).where(WriteOff.invoice_id == invoice.id, WriteOff.status == WriteOffStatus.PENDING)
    ):
        raise Conflict("A write-off for this bill is already waiting for the proprietor.")
    balance = _balance(session, invoice)
    if amount_kobo > balance:
        raise DomainError(f"Only ₦{balance / 100:,.2f} is owed on this bill.", field="amount_kobo")
    write_off = WriteOff(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        invoice_id=invoice.id,
        amount_kobo=amount_kobo,
        reason=reason[:300],
        requested_by=ctx.user.id,
    )
    session.add(write_off)
    session.flush()
    record(
        session,
        AuditAction.WRITE_OFF_REQUESTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=write_off,
        changes={"bill": invoice.number, "amount_kobo": amount_kobo, "reason": write_off.reason},
    )
    if _approves(ctx):  # the proprietor's own: nobody above to ask
        _apply(session, ctx, write_off, invoice)
    session.commit()
    return write_off


def _pending(session: Session, write_off_id: uuid.UUID) -> WriteOff:
    write_off = session.scalar(select(WriteOff).where(WriteOff.id == write_off_id).with_for_update())
    if write_off is None:
        raise NotFound("Write-off not found.")
    if write_off.status != WriteOffStatus.PENDING:
        raise Conflict(f"This write-off was already {write_off.status.value}.")
    return write_off


def approve(session: Session, ctx: StaffContext, write_off_id: uuid.UUID) -> WriteOff:
    if not _approves(ctx):
        raise PermissionDenied("Only the proprietor can approve a write-off.")
    write_off = _pending(session, write_off_id)
    invoice = _live_invoice(session, write_off.invoice_id)
    _apply(session, ctx, write_off, invoice)
    session.commit()
    return write_off


def reject(session: Session, ctx: StaffContext, write_off_id: uuid.UUID, reason: str) -> WriteOff:
    if not _approves(ctx):
        raise PermissionDenied("Only the proprietor can decide on a write-off.")
    if len(reason.strip()) < 3:
        raise DomainError("Say why it's rejected.", field="reason")
    write_off = _pending(session, write_off_id)
    write_off.status = WriteOffStatus.REJECTED
    write_off.decided_by, write_off.decided_at = ctx.user.id, datetime.now(UTC)
    write_off.rejection_reason = reason.strip()[:300]
    record(
        session,
        AuditAction.WRITE_OFF_REJECTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=write_off,
        changes={"amount_kobo": write_off.amount_kobo, "reason": write_off.rejection_reason},
    )
    session.commit()
    return write_off


@dataclass(frozen=True)
class WriteOffRow:
    write_off: WriteOff
    invoice_number: int
    student_id: uuid.UUID
    student_name: str
    admission_number: str


def listing(
    session: Session,
    *,
    status: WriteOffStatus | None = None,
    write_off_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[WriteOffRow]:
    """Write-offs, newest first, with the bill and pupil. One query."""
    stmt = (
        select(WriteOff, Invoice.number, Student.id, Student.first_name, Student.last_name, Student.admission_number)
        .join(Invoice, Invoice.id == WriteOff.invoice_id)
        .join(Student, Student.id == Invoice.student_id)
        .order_by(WriteOff.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(WriteOff.status == status)
    if write_off_id is not None:
        stmt = stmt.where(WriteOff.id == write_off_id)
    return [
        WriteOffRow(w, int(number), sid, f"{first} {last}", admission)
        for w, number, sid, first, last, admission in session.execute(stmt)
    ]
