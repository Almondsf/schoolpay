"""
Issuing receipts and looking them up.

`issue_receipts()` runs inside the transaction that records the payment, so a
payment and its receipt exist together or not at all. With numbers reserved
in that same transaction, receipt numbers have no gaps.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.sequences import reserve
from app.core.tenancy import scope_platform_wide
from app.modules.fees.invoicing import account_summaries
from app.modules.fees.models import Invoice
from app.modules.payments.allocation import NewAllocation
from app.modules.payments.models import Payment
from app.modules.schools.models import Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Student

from .models import Receipt

RECEIPT_SEQUENCE = "receipt"


def verification_url(code: str) -> str:
    return f"{get_settings().frontend_url}/verify/{code}"


def issue_receipts(
    session: Session, *, school_id: uuid.UUID, payments: list[Payment], allocations: list[NewAllocation]
) -> list[Receipt]:
    """
    One receipt per payment (snapshot included), numbered in the order given. A fixed
    number of queries however many payments; the caller commits.
    """
    if not payments:
        return []
    school = session.get(School, school_id)
    student_ids = list({p.student_id for p in payments})
    students = {s.id: s for s in session.scalars(select(Student).where(Student.id.in_(student_ids)))}
    if school is None or len(students) != len(student_ids):
        raise RuntimeError("Receipt for a payment whose school or student isn't visible in this session.")
    placements = {
        student_id: f"{level} {arm}"
        for student_id, level, arm in session.execute(
            select(Enrollment.student_id, ClassLevel.name, Arm.name)
            .join(Term, Term.id == Enrollment.term_id)
            .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
            .join(Arm, Arm.id == Enrollment.arm_id)
            .where(Enrollment.student_id.in_(student_ids), Term.is_current.is_(True))
        )
    }
    numbers = (
        dict(
            session.execute(
                select(Invoice.id, Invoice.number).where(Invoice.id.in_({a.invoice_id for a in allocations}))
            ).all()
        )
        if allocations
        else {}
    )
    owed_after = account_summaries(session, student_ids)
    receipt_numbers = reserve(session, school_id, RECEIPT_SEQUENCE, len(payments))
    receipts = []
    for payment, number in zip(payments, receipt_numbers, strict=True):
        student = students[payment.student_id]
        mine = [a for a in allocations if a.payment_id == payment.id]
        snapshot: dict[str, Any] = {
            "school": {"name": school.name, "address": school.address, "phone": school.phone, "email": school.email},
            "student": {
                "first_name": student.first_name,
                "last_name": student.last_name,
                "full_name": student.full_name,
                "admission_number": student.admission_number,
                "class": placements.get(student.id, ""),
            },
            "payment": {
                "method": payment.method.value,
                "reference": payment.reference or payment.gateway_reference or "",
                "received_at": payment.received_at.isoformat(),
            },
            "applied": [{"invoice_number": numbers.get(a.invoice_id), "amount_kobo": a.amount_kobo} for a in mine],
            "credit_kobo": payment.amount_kobo - sum(a.amount_kobo for a in mine),
            "balance_after_kobo": owed_after[student.id].outstanding_kobo,
        }
        receipts.append(
            Receipt(
                id=uuid.uuid4(),
                school_id=school_id,
                number=number,
                payment_id=payment.id,
                student_id=student.id,
                amount_kobo=payment.amount_kobo,
                verification_code=secrets.token_urlsafe(16),
                snapshot=snapshot,
            )
        )
    session.add_all(receipts)
    session.flush()
    return receipts


def receipts_for_payments(session: Session, payment_ids: list[uuid.UUID]) -> dict[uuid.UUID, Receipt]:
    if not payment_ids:
        return {}
    return {r.payment_id: r for r in session.scalars(select(Receipt).where(Receipt.payment_id.in_(payment_ids)))}


def is_reversed(session: Session, payment_id: uuid.UUID) -> bool:
    return session.scalar(select(Payment.id).where(Payment.reverses_payment_id == payment_id)) is not None


@dataclass(frozen=True)
class PublicReceipt:
    """Everything the public verification page may show, and nothing more."""

    school_name: str
    receipt_number: int
    amount_kobo: int
    paid_on: datetime
    status: str  # "valid" | "reversed"
    student_initials: str


def initials(first_name: str, last_name: str) -> str:
    return "".join(f"{part[0].upper()}." for part in (first_name, last_name) if part)


def public_lookup(code: str) -> PublicReceipt | None:
    """Look a receipt up by its verification code, across schools (the code is the only key)."""
    with SessionLocal() as session:
        scope_platform_wide(session)
        receipt = session.scalar(select(Receipt).where(Receipt.verification_code == code))
        if receipt is None:
            return None
        snap = receipt.snapshot
        return PublicReceipt(
            school_name=snap["school"]["name"],
            receipt_number=receipt.number,
            amount_kobo=receipt.amount_kobo,
            paid_on=datetime.fromisoformat(snap["payment"]["received_at"]),
            status="reversed" if is_reversed(session, receipt.payment_id) else "valid",
            student_initials=initials(snap["student"]["first_name"], snap["student"]["last_name"]),
        )
