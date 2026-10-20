"""
Fee status by pupil: for a term, each pupil's fees one by one (tuition, bus, WAEC...) as
paid, part paid or unpaid with what's left, plus earlier debts and the total owed.

Payments are applied to whole bills, not to single fees, so a bill's payments are shown
as covering its fees in the order they appear on the bill. A discount reduces the fee it
is for; a discount for the whole bill reduces its first fee. Four queries for any number
of pupils. The session must be scoped to the school.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.repo import get_or_404
from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Discount, FeeItem, Invoice, InvoiceLine, LineKind
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, Term
from app.modules.students.models import Enrollment, Student

OTHER = "Other charges"


@dataclass
class FeeCell:
    billed_kobo: int = 0
    left_kobo: int = 0

    @property
    def status(self) -> str:
        if self.billed_kobo <= 0:
            return "Not charged"
        if self.left_kobo <= 0:
            return "Paid"
        return "Unpaid" if self.left_kobo >= self.billed_kobo else "Part paid"


@dataclass
class PupilStatus:
    full_name: str
    admission_number: str
    class_name: str
    fees: dict[str, FeeCell] = field(default_factory=dict)  # by column name
    earlier_left_kobo: int = 0  # older terms, balances brought forward, one-off charges without a term

    @property
    def term_billed_kobo(self) -> int:
        return sum(max(c.billed_kobo, 0) for c in self.fees.values())

    @property
    def term_left_kobo(self) -> int:
        return sum(c.left_kobo for c in self.fees.values())

    @property
    def term_paid_kobo(self) -> int:
        return self.term_billed_kobo - self.term_left_kobo

    @property
    def total_left_kobo(self) -> int:
        return self.term_left_kobo + self.earlier_left_kobo

    @property
    def status(self) -> str:
        if self.term_billed_kobo <= 0 and self.earlier_left_kobo <= 0:
            return "Nothing billed"
        if self.total_left_kobo <= 0:
            return "Paid"
        return "Unpaid" if self.term_left_kobo >= self.term_billed_kobo and self.term_billed_kobo > 0 else "Part paid"


@dataclass(frozen=True)
class FeeStatusReport:
    term_label: str
    scope: str  # "All classes" or the class's name
    columns: list[str]  # fee names, in bill order
    pupils: list[PupilStatus]


def fee_status(session: Session, term_id: uuid.UUID, class_level_id: uuid.UUID | None = None) -> FeeStatusReport:
    term = get_or_404(session, Term, term_id, label="Term", field="term_id")
    session_name = session.scalar(select(AcademicSession.name).where(AcademicSession.id == term.session_id)) or ""
    scope = (
        get_or_404(session, ClassLevel, class_level_id, label="Class", field="class_level_id").name
        if class_level_id
        else "All classes"
    )

    placed = (
        select(Student, ClassLevel.name, Arm.name)
        .join(Enrollment, and_(Enrollment.student_id == Student.id, Enrollment.term_id == term.id))
        .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
        .join(Arm, Arm.id == Enrollment.arm_id)
        .where(Student.erased_at.is_(None))
        .order_by(ClassLevel.rank, Arm.name, Student.last_name, Student.first_name, Student.admission_number)
    )
    if class_level_id:
        placed = placed.where(Enrollment.class_level_id == class_level_id)
    students = session.execute(placed).all()
    pupils = {s.id: PupilStatus(s.full_name, s.admission_number, f"{level} {arm}") for s, level, arm in students}
    if not pupils:
        return FeeStatusReport(f"{session_name} {term.label}", scope, [], [])

    totals, paid = totals_subquery(), paid_subquery()
    bills = session.execute(
        select(
            Invoice.id,
            Invoice.student_id,
            Invoice.term_id,
            func.coalesce(totals.c.total_kobo, 0),
            func.coalesce(paid.c.paid_kobo, 0),
        )
        .outerjoin(totals, totals.c.invoice_id == Invoice.id)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(Invoice.student_id.in_(list(pupils)), Invoice.voided_at.is_(None))
    ).all()
    this_term = {inv: (student, int(done)) for inv, student, t, _, done in bills if t == term.id}
    for _, student, t, total, done in bills:
        if t != term.id:
            pupils[student].earlier_left_kobo += max(int(total) - int(done), 0)

    # Each bill's lines in bill order, labelled by the fee they belong to
    fee_of_line = func.coalesce(InvoiceLine.fee_item_id, Discount.fee_item_id)
    lines = session.execute(
        select(InvoiceLine.invoice_id, InvoiceLine.kind, InvoiceLine.amount_kobo, fee_of_line, FeeItem.name)
        .outerjoin(Discount, Discount.id == InvoiceLine.discount_id)
        .outerjoin(FeeItem, FeeItem.id == fee_of_line)
        .where(InvoiceLine.invoice_id.in_(list(this_term)))
        .order_by(InvoiceLine.invoice_id, InvoiceLine.position)
    ).all()
    by_bill: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)  # bill -> fee name -> net, in bill order
    first_seen: dict[str, int] = {}
    for n, (invoice_id, kind, amount, fee_item_id, fee_name) in enumerate(lines):
        fees = by_bill[invoice_id]
        if kind == LineKind.DISCOUNT and fee_item_id is None and fees:
            name = next(iter(fees))  # a discount on the whole bill reduces its first fee
        else:
            name = fee_name if fee_item_id is not None and fee_name else OTHER
        fees[name] = fees.get(name, 0) + int(amount)
        first_seen.setdefault(name, n)

    for invoice_id, fees in by_bill.items():
        student, left_to_spread = this_term[invoice_id]
        cells = pupils[student].fees
        for name, net in fees.items():
            covered = min(max(net, 0), left_to_spread)
            left_to_spread -= covered
            cell = cells.setdefault(name, FeeCell())
            cell.billed_kobo += net
            cell.left_kobo += max(net, 0) - covered

    columns = sorted(first_seen, key=lambda name: (name == OTHER, first_seen[name]))
    return FeeStatusReport(f"{session_name} {term.label}", scope, columns, list(pupils.values()))
