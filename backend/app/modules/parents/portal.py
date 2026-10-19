"""
What a parent sees: their own children only. Every lookup goes through the
guardian's links, so another child's ID is simply "not found", even within
the same school.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.modules.fees.invoicing import AccountSummary, account_summaries
from app.modules.fees.models import Invoice
from app.modules.payments.allocation import credit_by_student
from app.modules.payments.checkout import checkout_email
from app.modules.payments.models import VirtualAccount, VirtualAccountStatus
from app.modules.receipts.models import Receipt
from app.modules.schools.models import Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Guardian, GuardianStudent, Student

from .deps import ParentContext


def require_child(session: Session, ctx: ParentContext, student_id: uuid.UUID) -> Student:
    student = session.scalar(
        select(Student)
        .join(
            GuardianStudent,
            and_(GuardianStudent.student_id == Student.id, GuardianStudent.guardian_id == ctx.guardian_id),
        )
        .where(Student.id == student_id)
    )
    if student is None:
        raise NotFound("Child not found.")
    return student


def require_child_invoice(session: Session, ctx: ParentContext, invoice_id: uuid.UUID) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise NotFound("Invoice not found.")
    require_child(session, ctx, invoice.student_id)
    return invoice


def require_child_receipt(session: Session, ctx: ParentContext, receipt_id: uuid.UUID) -> Receipt:
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        raise NotFound("Receipt not found.")
    require_child(session, ctx, receipt.student_id)
    return receipt


@dataclass(frozen=True)
class ChildOverview:
    student: Student
    class_name: str | None
    summary: AccountSummary
    credit_kobo: int
    virtual_account: VirtualAccount | None


@dataclass(frozen=True)
class Household:
    guardian_name: str
    school_name: str
    children: list[ChildOverview]


def household(session: Session, ctx: ParentContext) -> Household:
    """The parent's children with class, balance, credit and account number, in a fixed number of queries."""
    guardian = session.get(Guardian, ctx.guardian_id)
    school = session.get(School, ctx.school_id)
    if guardian is None or school is None:
        raise NotFound("Not found.")
    students = list(
        session.scalars(
            select(Student)
            .join(GuardianStudent, GuardianStudent.student_id == Student.id)
            .where(GuardianStudent.guardian_id == ctx.guardian_id)
            .order_by(Student.first_name)
        )
    )
    ids = [s.id for s in students]
    classes = (
        dict(
            session.execute(
                select(Enrollment.student_id, ClassLevel.name + " " + Arm.name)
                .select_from(Enrollment)
                .join(Term, and_(Term.id == Enrollment.term_id, Term.is_current.is_(True)))
                .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
                .join(Arm, Arm.id == Enrollment.arm_id)
                .where(Enrollment.student_id.in_(ids))
            ).all()
        )
        if ids
        else {}
    )
    accounts = (
        {
            va.student_id: va
            for va in session.scalars(
                select(VirtualAccount).where(
                    VirtualAccount.student_id.in_(ids), VirtualAccount.status == VirtualAccountStatus.ACTIVE
                )
            )
        }
        if ids
        else {}
    )
    summaries, credits = account_summaries(session, ids), credit_by_student(session, ids)
    return Household(
        guardian_name=guardian.full_name,
        school_name=school.name,
        children=[
            ChildOverview(s, classes.get(s.id), summaries[s.id], credits[s.id], accounts.get(s.id)) for s in students
        ],
    )


def parent_email(session: Session, ctx: ParentContext) -> str:
    """For card checkout: the parent's email, or a routing address if the school has none on file."""
    guardian = session.get(Guardian, ctx.guardian_id)
    school = session.get(School, ctx.school_id)
    if guardian is None or school is None:
        raise NotFound("Not found.")
    return checkout_email(guardian, school)
