"""
Applying money to invoices.

A student's unallocated money (from any of their payments) goes to their
outstanding invoices **oldest first**: an opening balance before anything else,
then invoices in the order they were issued. What can't be applied stays as
credit and is applied automatically when new invoices are issued.

Credit is never stored. It's payments minus allocations, per student.

`allocate()` locks the students' rows first, so two webhooks for the same
student can't both spend the same credit. It handles any number of students
in a fixed number of queries.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Invoice, InvoiceKind
from app.modules.students.models import Student

from .models import Allocation, Payment


@dataclass(frozen=True)
class NewAllocation:
    payment_id: uuid.UUID
    invoice_id: uuid.UUID
    student_id: uuid.UUID
    amount_kobo: int


@dataclass
class _Owing:
    invoice_id: uuid.UUID
    due: int


def unallocated_by_payment(session: Session, student_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """(payment_id, student_id, unallocated kobo) for payments with money left, oldest first."""
    allocated = (
        select(Allocation.payment_id, func.sum(Allocation.amount_kobo).label("allocated"))
        .group_by(Allocation.payment_id)
        .subquery()
    )
    remaining = Payment.amount_kobo - func.coalesce(allocated.c.allocated, 0)
    rows = session.execute(
        select(Payment.id, Payment.student_id, remaining)
        .outerjoin(allocated, allocated.c.payment_id == Payment.id)
        .where(Payment.student_id.in_(student_ids), remaining != 0)
        .order_by(Payment.received_at, Payment.created_at)
    ).all()
    return [(pid, sid, int(left)) for pid, sid, left in rows]


def credit_by_student(session: Session, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Money received but not yet applied to an invoice, per student."""
    totals: dict[uuid.UUID, int] = dict.fromkeys(student_ids, 0)
    for _payment_id, student_id, left in unallocated_by_payment(session, student_ids):
        totals[student_id] += left
    return totals


def allocate(session: Session, student_ids: list[uuid.UUID]) -> list[NewAllocation]:
    """
    Apply every listed student's unallocated money to their outstanding invoices,
    oldest first. Adds the allocations to the session; the caller commits.
    """
    if not student_ids:
        return []
    # Serialise per student: concurrent payments for the same student queue here
    session.execute(select(Student.id).where(Student.id.in_(student_ids)).order_by(Student.id).with_for_update())

    money = [(pid, sid, left) for pid, sid, left in unallocated_by_payment(session, student_ids) if left > 0]
    if not money:
        return []
    totals, paid = totals_subquery(), paid_subquery()
    balance = func.coalesce(totals.c.total_kobo, 0) - func.coalesce(paid.c.paid_kobo, 0)
    owing = session.execute(
        select(Invoice.id, Invoice.student_id, balance)
        .outerjoin(totals, totals.c.invoice_id == Invoice.id)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(Invoice.student_id.in_({sid for _, sid, _ in money}), Invoice.voided_at.is_(None), balance > 0)
        .order_by(case((Invoice.kind == InvoiceKind.OPENING_BALANCE, 0), else_=1), Invoice.issued_at, Invoice.number)
    ).all()
    invoices_by_student: dict[uuid.UUID, list[_Owing]] = defaultdict(list)
    for invoice_id, student_id, left in owing:
        invoices_by_student[student_id].append(_Owing(invoice_id, int(left)))

    created: list[NewAllocation] = []
    for payment_id, student_id, available in money:
        for owing_invoice in invoices_by_student.get(student_id, []):
            if available == 0:
                break
            amount = min(available, owing_invoice.due)
            if amount == 0:
                continue
            created.append(NewAllocation(payment_id, owing_invoice.invoice_id, student_id, amount))
            owing_invoice.due -= amount
            available -= amount
    session.add_all(
        Allocation(payment_id=a.payment_id, invoice_id=a.invoice_id, amount_kobo=a.amount_kobo) for a in created
    )
    session.flush()
    return created
