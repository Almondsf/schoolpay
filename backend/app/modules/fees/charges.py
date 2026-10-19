"""
Extra charges after the term has started.

**Class charges** (`charge_classes`). A fee for whole classes, such as WAEC for
SSS 3 or an end-of-year party. It goes on the term's fee list for those classes,
so bills generated later include it, and it's added as a line to every bill
already issued in those classes. Pupils who aren't taking part are excluded:
they get an exemption, so regenerating can't add it back.

**Exemptions** (`exempt`, `unexempt`). One pupil doesn't pay a class fee this
term. If they've already been billed for it, a correcting line takes it off
(lines are never edited). Undoing the exemption charges it again.

**Individual extras** (`charge_pupil`). An item for one pupil, such as a uniform
or textbooks, priced from the school's price list (`FeeItem.default_amount_kobo`)
or typed in, with a quantity. It goes on the pupil's bill for the current term,
or on a one-off bill if they haven't been billed this term.

Every change is audited, and each is a fixed number of statements however many
pupils it touches.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound
from app.core.money import format_naira
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.schools.models import Arm, ClassLevel, Term
from app.modules.students.models import Enrollment, Student, StudentStatus

from .invoicing import DraftLine, _apply_credit, amounts_for, create_invoice
from .models import (
    FeeCategory,
    FeeItem,
    FeeSchedule,
    Invoice,
    InvoiceKind,
    InvoiceLine,
    LineKind,
    StudentFeeExemption,
)
from .setup_service import MAX_AMOUNT_KOBO


def _term_invoices(session: Session, term_id: uuid.UUID, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """student_id -> their live term bill for this term."""
    if not student_ids:
        return {}
    return dict(
        session.execute(
            select(Invoice.student_id, Invoice.id).where(
                Invoice.term_id == term_id,
                Invoice.kind == InvoiceKind.TERM,
                Invoice.voided_at.is_(None),
                Invoice.student_id.in_(student_ids),
            )
        ).all()
    )


def _next_positions(session: Session, invoice_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not invoice_ids:
        return {}
    used = dict(
        session.execute(
            select(InvoiceLine.invoice_id, func.max(InvoiceLine.position))
            .where(InvoiceLine.invoice_id.in_(invoice_ids))
            .group_by(InvoiceLine.invoice_id)
        ).all()
    )
    return {iid: int(used.get(iid) or 0) + 1 for iid in invoice_ids}


# --- Class charges ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChargeStudent:
    student_id: uuid.UUID
    full_name: str
    admission_number: str
    class_name: str
    billed: bool  # has this term's bill already (gets the line now); otherwise it comes with generation
    excluded: bool


@dataclass
class ClassChargeReport:
    dry_run: bool
    item_name: str
    amount_kobo: int
    students: list[ChargeStudent] = field(default_factory=list)

    @property
    def charged_now(self) -> int:
        return sum(1 for s in self.students if s.billed and not s.excluded)

    @property
    def total_kobo(self) -> int:
        return sum(self.amount_kobo for s in self.students if not s.excluded)


def charge_classes(
    session: Session,
    ctx: StaffContext,
    *,
    term_id: uuid.UUID,
    fee_item_id: uuid.UUID | None,
    new_item_name: str | None,
    amount_kobo: int,
    class_level_ids: set[uuid.UUID],
    excluded_student_ids: set[uuid.UUID],
    dry_run: bool,
) -> ClassChargeReport:
    """Add a fee to whole classes for a term, now and for bills generated later. Fixed number of statements."""
    term = get_or_404(session, Term, term_id, label="Term")
    if not 0 < amount_kobo <= MAX_AMOUNT_KOBO:
        raise DomainError("Enter the amount.", field="amount_kobo")
    if not class_level_ids:
        raise DomainError("Choose at least one class.", field="class_level_ids")
    levels = dict(
        session.execute(select(ClassLevel.id, ClassLevel.name).where(ClassLevel.id.in_(class_level_ids))).all()
    )
    if set(levels) != class_level_ids:
        raise DomainError("Unknown class.", field="class_level_ids")

    item: FeeItem | None = None
    if fee_item_id is not None:
        item = get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id")
        name = item.name
        clashing = list(
            session.scalars(
                select(ClassLevel.name)
                .join(FeeSchedule, FeeSchedule.class_level_id == ClassLevel.id)
                .where(
                    FeeSchedule.term_id == term.id,
                    FeeSchedule.fee_item_id == item.id,
                    FeeSchedule.class_level_id.in_(class_level_ids),
                )
            )
        )
        if clashing:
            raise Conflict(f"{name} is already a fee for {', '.join(sorted(clashing))} this term.", field="fee_item_id")
    elif new_item_name and new_item_name.strip():
        name = new_item_name.strip()
        if session.scalar(select(FeeItem.id).where(func.lower(FeeItem.name) == name.lower())):
            raise Conflict(f"There's already a fee called {name}: choose it from the list.", field="new_item_name")
    else:
        raise DomainError("Choose a fee or name a new one.", field="fee_item_id")

    rows = session.execute(
        select(Student.id, Student.first_name, Student.last_name, Student.admission_number, ClassLevel.name, Arm.name)
        .join(Enrollment, Enrollment.student_id == Student.id)
        .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
        .join(Arm, Arm.id == Enrollment.arm_id)
        .where(
            Enrollment.term_id == term.id,
            Enrollment.class_level_id.in_(class_level_ids),
            Student.status == StudentStatus.ACTIVE,
        )
        .order_by(ClassLevel.rank, Arm.name, Student.last_name, Student.first_name)
    ).all()
    in_classes = {r[0] for r in rows}
    if not excluded_student_ids <= in_classes:
        raise DomainError("Some excluded pupils aren't in those classes this term.", field="excluded_student_ids")
    billed = _term_invoices(session, term.id, list(in_classes))
    report = ClassChargeReport(dry_run=dry_run, item_name=name, amount_kobo=amount_kobo)
    report.students = [
        ChargeStudent(sid, f"{first} {last}", admission, f"{level} {arm}", sid in billed, sid in excluded_student_ids)
        for sid, first, last, admission, level, arm in rows
    ]
    if dry_run:
        session.rollback()
        return report

    if item is None:
        item = FeeItem(id=uuid.uuid4(), name=name, category=FeeCategory.OTHER)
        session.add(item)
        try:
            session.flush()
        except IntegrityError as e:
            session.rollback()
            raise Conflict(f"There's already a fee called {name}.", field="new_item_name") from e
    session.add_all(
        FeeSchedule(term_id=term.id, class_level_id=level_id, fee_item_id=item.id, amount_kobo=amount_kobo)
        for level_id in class_level_ids
    )
    session.add_all(
        StudentFeeExemption(
            student_id=sid, term_id=term.id, fee_item_id=item.id, reason="Not taking part", created_by=ctx.user.id
        )
        for sid in excluded_student_ids
    )
    to_charge = {sid: iid for sid, iid in billed.items() if sid not in excluded_student_ids}
    positions = _next_positions(session, list(to_charge.values()))
    session.add_all(
        InvoiceLine(
            invoice_id=iid,
            position=positions[iid],
            kind=LineKind.FEE,
            description=name,
            amount_kobo=amount_kobo,
            fee_item_id=item.id,
            created_by=ctx.user.id,
            reason="Added to the class after billing",
        )
        for iid in to_charge.values()
    )
    session.flush()
    _apply_credit(session, list(to_charge))  # money already paid ahead covers the new line
    record(
        session,
        AuditAction.CLASS_CHARGE_ADDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=term,
        changes={
            "item": name,
            "amount_kobo": amount_kobo,
            "classes": sorted(levels.values()),
            "charged_now": len(to_charge),
            "excluded": len(excluded_student_ids),
        },
    )
    session.commit()
    return report


# --- Exemptions ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    term_id: uuid.UUID
    fee_item_id: uuid.UUID
    item_name: str
    reason: str


def exemptions(session: Session, student_id: uuid.UUID) -> list[Exemption]:
    return [
        Exemption(term_id, item_id, name, reason)
        for term_id, item_id, name, reason in session.execute(
            select(
                StudentFeeExemption.term_id, StudentFeeExemption.fee_item_id, FeeItem.name, StudentFeeExemption.reason
            )
            .join(FeeItem, FeeItem.id == StudentFeeExemption.fee_item_id)
            .where(StudentFeeExemption.student_id == student_id)
            .order_by(FeeItem.name)
        ).all()
    ]


def _item_net(session: Session, invoice_id: uuid.UUID, fee_item_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(InvoiceLine.amount_kobo), 0)).where(
                InvoiceLine.invoice_id == invoice_id, InvoiceLine.fee_item_id == fee_item_id
            )
        )
        or 0
    )


def exempt(
    session: Session, ctx: StaffContext, student_id: uuid.UUID, term_id: uuid.UUID, fee_item_id: uuid.UUID, reason: str
) -> int:
    """Take a class fee off one pupil for a term. Returns what came off their bill (kobo), if anything."""
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    term = get_or_404(session, Term, term_id, label="Term", field="term_id")
    item = get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id")
    if session.get(StudentFeeExemption, (student.id, term.id, item.id)):
        raise Conflict(f"{student.first_name} is already exempt from {item.name} this term.")
    invoice_id = _term_invoices(session, term.id, [student.id]).get(student.id)
    removed = _item_net(session, invoice_id, item.id) if invoice_id else 0
    if invoice_id and removed > 0:
        amounts = amounts_for(session, [invoice_id])[invoice_id]
        if amounts.total_kobo - removed < amounts.paid_kobo:
            raise Conflict(
                f"{format_naira(amounts.paid_kobo)} has been paid on this bill, so {item.name} can't come off it. "
                "Reverse a payment first, or leave the fee on."
            )
        positions = _next_positions(session, [invoice_id])
        session.add(
            InvoiceLine(
                invoice_id=invoice_id,
                position=positions[invoice_id],
                kind=LineKind.ADJUSTMENT,
                description=f"Exempted: {item.name}",
                amount_kobo=-removed,
                fee_item_id=item.id,
                created_by=ctx.user.id,
                reason=reason.strip()[:300] or "Not taking part",
            )
        )
    session.add(
        StudentFeeExemption(
            student_id=student.id,
            term_id=term.id,
            fee_item_id=item.id,
            reason=reason.strip()[:300],
            created_by=ctx.user.id,
        )
    )
    record(
        session,
        AuditAction.FEE_EXEMPTION_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"exempt": True, "item": item.name, "removed_kobo": removed, "reason": reason.strip()[:300]},
    )
    session.commit()
    return removed


def unexempt(
    session: Session, ctx: StaffContext, student_id: uuid.UUID, term_id: uuid.UUID, fee_item_id: uuid.UUID
) -> int:
    """Charge a pupil a class fee they were exempted from. Returns what went back on their bill (kobo)."""
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    item = get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id")
    gone = session.execute(
        delete(StudentFeeExemption)
        .where(
            StudentFeeExemption.student_id == student.id,
            StudentFeeExemption.term_id == term_id,
            StudentFeeExemption.fee_item_id == item.id,
        )
        .returning(StudentFeeExemption.fee_item_id)
    ).all()
    if not gone:
        raise NotFound("That exemption doesn't exist.")
    added = 0
    invoice_id = _term_invoices(session, term_id, [student.id]).get(student.id)
    if invoice_id and _item_net(session, invoice_id, item.id) <= 0:
        price = session.scalar(
            select(FeeSchedule.amount_kobo)
            .join(
                Enrollment,
                and_(
                    Enrollment.class_level_id == FeeSchedule.class_level_id, Enrollment.term_id == FeeSchedule.term_id
                ),
            )
            .where(
                Enrollment.student_id == student.id,
                FeeSchedule.term_id == term_id,
                FeeSchedule.fee_item_id == item.id,
                FeeSchedule.is_optional.is_(False),
            )
        )
        if price:
            positions = _next_positions(session, [invoice_id])
            session.add(
                InvoiceLine(
                    invoice_id=invoice_id,
                    position=positions[invoice_id],
                    kind=LineKind.FEE,
                    description=item.name,
                    amount_kobo=int(price),
                    fee_item_id=item.id,
                    created_by=ctx.user.id,
                    reason="Exemption removed",
                )
            )
            added = int(price)
            session.flush()
            _apply_credit(session, [student.id])
    record(
        session,
        AuditAction.FEE_EXEMPTION_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"exempt": False, "item": item.name, "added_kobo": added},
    )
    session.commit()
    return added


# --- Individual extras -------------------------------------------------------------------------------


def charge_pupil(
    session: Session,
    ctx: StaffContext,
    student_id: uuid.UUID,
    *,
    fee_item_id: uuid.UUID | None,
    description: str,
    unit_kobo: int | None,
    quantity: int,
    reason: str,
) -> uuid.UUID:
    """An item for one pupil (uniform, textbooks). Returns the bill it went on."""
    student = get_or_404(session, Student, student_id, label="Student")
    item = get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id") if fee_item_id else None
    unit = unit_kobo if unit_kobo is not None else (item.default_amount_kobo if item else None)
    if not unit or unit <= 0:
        raise DomainError("Enter the price.", field="unit_kobo")
    if not 1 <= quantity <= 100:
        raise DomainError("Quantity must be between 1 and 100.", field="quantity")
    amount = unit * quantity
    if amount > MAX_AMOUNT_KOBO:
        raise DomainError("That's more than a bill can hold.", field="quantity")
    name = (description.strip() or (item.name if item else "")).strip()
    if not name:
        raise DomainError("Say what the charge is for.", field="description")
    label = f"{name} x {quantity}" if quantity > 1 else name

    term_id = session.scalar(select(Term.id).where(Term.is_current.is_(True)))
    invoice_id = _term_invoices(session, term_id, [student.id]).get(student.id) if term_id else None
    if invoice_id is None:  # not billed this term: a one-off bill of its own (create_invoice audits and commits)
        invoice = create_invoice(
            session,
            ctx,
            student_id=student.id,
            kind=InvoiceKind.ADHOC,
            lines=[DraftLine(LineKind.FEE, label, amount, fee_item_id=item.id if item else None)],
            due_date=None,
        )
        return invoice.id
    positions = _next_positions(session, [invoice_id])
    session.add(
        InvoiceLine(
            invoice_id=invoice_id,
            position=positions[invoice_id],
            kind=LineKind.FEE,
            description=label,
            amount_kobo=amount,
            fee_item_id=item.id if item else None,
            created_by=ctx.user.id,
            reason=reason.strip()[:300] or None,
        )
    )
    session.flush()
    _apply_credit(session, [student.id])
    record(
        session,
        AuditAction.PUPIL_CHARGE_ADDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"item": label, "amount_kobo": amount, "invoice": invoice_id},
    )
    session.commit()
    return invoice_id
