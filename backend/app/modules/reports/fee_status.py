"""
Fee status by pupil: for a term, each pupil's fees one by one (tuition, bus, WAEC...) as
paid, part paid or unpaid with what's left, plus earlier debts and the total owed. Used by
the downloadable report (`fee_status`) and by the breakdowns on the payment page, the
parent portal and the staff pupil page (`fee_breakdowns`), so they always agree.

Payments are applied to whole bills, not to single fees. Money a payer meant for chosen
fees (`PaymentEarmark`) covers those fees first; the rest of a bill's payments are shown
covering its fees in the order they appear on the bill. A discount reduces the fee it is
for; a discount for the whole bill reduces its first fee. A fixed number of queries for
any number of pupils. The session must be scoped to the school.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.repo import get_or_404
from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Discount, FeeItem, Invoice, InvoiceLine, LineKind
from app.modules.payments.models import Payment, PaymentEarmark
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, Term
from app.modules.students.models import Enrollment, Student

OTHER = "Other charges"


@dataclass
class FeeCell:
    billed_kobo: int = 0
    left_kobo: int = 0

    @property
    def paid_kobo(self) -> int:
        return max(self.billed_kobo, 0) - self.left_kobo

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
    fees: dict[str, FeeCell] = field(default_factory=dict)  # by fee name, in bill order
    # Where each fee sits: name -> [(bill, fee item, description, left on that bill)], to earmark a payment
    targets: dict[str, list[tuple[uuid.UUID, uuid.UUID | None, str, int]]] = field(default_factory=dict)
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


def _term_label(session: Session, term: Term) -> str:
    session_name = session.scalar(select(AcademicSession.name).where(AcademicSession.id == term.session_id)) or ""
    return f"{session_name} {term.label}"


def _fill(
    session: Session, term_id: uuid.UUID | None, pupils: dict[uuid.UUID, PupilStatus], *, describe_others: bool
) -> list[str]:
    """
    Work out each pupil's fees for the term and their earlier debts. Returns the fee names in
    bill order. `describe_others`: name one-off lines by their description, not "Other charges".
    """
    if not pupils:
        return []
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
    this_term = {inv: (student, int(done)) for inv, student, t, _, done in bills if term_id and t == term_id}
    for invoice_id, student, _, total, done in bills:
        if invoice_id not in this_term:
            pupils[student].earlier_left_kobo += max(int(total) - int(done), 0)
    if not this_term:
        return []

    # Each bill's lines in bill order, labelled by the fee they belong to
    fee_of_line = func.coalesce(InvoiceLine.fee_item_id, Discount.fee_item_id)
    lines = session.execute(
        select(
            InvoiceLine.invoice_id,
            InvoiceLine.kind,
            InvoiceLine.amount_kobo,
            InvoiceLine.description,
            fee_of_line,
            FeeItem.name,
        )
        .outerjoin(Discount, Discount.id == InvoiceLine.discount_id)
        .outerjoin(FeeItem, FeeItem.id == fee_of_line)
        .where(InvoiceLine.invoice_id.in_(list(this_term)))
        .order_by(InvoiceLine.invoice_id, InvoiceLine.position)
    ).all()
    by_bill: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)  # bill -> fee name -> net, in bill order
    identity: dict[tuple[uuid.UUID, str], tuple[uuid.UUID | None, str]] = {}  # (bill, name) -> (fee item, text)
    first_seen: dict[str, int] = {}

    def label(fee_item_id: uuid.UUID | None, fee_name: str | None, description: str) -> str:
        if fee_item_id is not None and fee_name:
            return fee_name
        return description if describe_others and description else OTHER

    for n, (invoice_id, kind, amount, description, fee_item_id, fee_name) in enumerate(lines):
        fees = by_bill[invoice_id]
        if kind == LineKind.DISCOUNT and fee_item_id is None and fees:
            name = next(iter(fees))  # a discount on the whole bill reduces its first fee
        else:
            name = label(fee_item_id, fee_name, description)
            identity.setdefault((invoice_id, name), (fee_item_id, "" if fee_item_id else description))
        fees[name] = fees.get(name, 0) + int(amount)
        first_seen.setdefault(name, n)

    # Money the payers meant for particular fees (payments not reversed)
    reversed_ = select(Payment.reverses_payment_id).where(Payment.reverses_payment_id.is_not(None))
    earmarked: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
    for invoice_id, mark_item, mark_name, mark_text, amount in session.execute(
        select(
            PaymentEarmark.invoice_id,
            PaymentEarmark.fee_item_id,
            FeeItem.name,
            PaymentEarmark.description,
            func.sum(PaymentEarmark.amount_kobo),
        )
        .outerjoin(FeeItem, FeeItem.id == PaymentEarmark.fee_item_id)
        .where(PaymentEarmark.invoice_id.in_(list(this_term)), PaymentEarmark.payment_id.not_in(reversed_))
        .group_by(PaymentEarmark.invoice_id, PaymentEarmark.fee_item_id, FeeItem.name, PaymentEarmark.description)
    ):
        earmarked[(invoice_id, label(mark_item, mark_name, mark_text))] += int(amount)

    for invoice_id, fees in by_bill.items():
        student, left_to_spread = this_term[invoice_id]
        covered: dict[str, int] = {}
        for name, net in fees.items():  # first what was paid for particular fees
            covered[name] = min(max(net, 0), earmarked.get((invoice_id, name), 0), left_to_spread)
            left_to_spread -= covered[name]
        for name, net in fees.items():  # then the rest, in bill order
            more = min(max(net, 0) - covered[name], left_to_spread)
            covered[name] += more
            left_to_spread -= more
        pupil = pupils[student]
        for name, net in fees.items():
            cell = pupil.fees.setdefault(name, FeeCell())
            cell.billed_kobo += net
            left = max(net, 0) - covered[name]
            cell.left_kobo += left
            if left > 0 and (invoice_id, name) in identity:
                item_ref, text = identity[(invoice_id, name)]
                pupil.targets.setdefault(name, []).append((invoice_id, item_ref, text, left))
    return sorted(first_seen, key=lambda name: (name == OTHER, first_seen[name]))


def fee_status(session: Session, term_id: uuid.UUID, class_level_id: uuid.UUID | None = None) -> FeeStatusReport:
    """Every pupil placed in the term (all classes, or one), for the download."""
    term = get_or_404(session, Term, term_id, label="Term", field="term_id")
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
    pupils = {
        s.id: PupilStatus(s.full_name, s.admission_number, f"{level} {arm}")
        for s, level, arm in session.execute(placed).all()
    }
    columns = _fill(session, term.id, pupils, describe_others=False)
    return FeeStatusReport(_term_label(session, term), scope, columns, list(pupils.values()))


@dataclass(frozen=True)
class FeeLine:
    name: str
    billed_kobo: int
    paid_kobo: int
    left_kobo: int
    status: str


@dataclass(frozen=True)
class FeeBreakdown:
    """What a pupil's balance is made of: this term's fees one by one, then earlier debts."""

    term_label: str | None
    fees: list[FeeLine]
    earlier_left_kobo: int

    @property
    def total_left_kobo(self) -> int:
        return sum(f.left_kobo for f in self.fees) + self.earlier_left_kobo


def fee_breakdowns(
    session: Session, student_ids: list[uuid.UUID], term_id: uuid.UUID | None = None
) -> dict[uuid.UUID, FeeBreakdown]:
    """For a few pupils (a family, one pupil): the term's fees (default: the current term), in bill order."""
    if not student_ids:
        return {}
    term = (
        get_or_404(session, Term, term_id, label="Term", field="term_id")
        if term_id
        else session.scalar(select(Term).where(Term.is_current.is_(True)))
    )
    pupils = {sid: PupilStatus("", "", "") for sid in student_ids}
    _fill(session, term.id if term else None, pupils, describe_others=True)
    label = _term_label(session, term) if term else None
    return {
        sid: FeeBreakdown(
            term_label=label,
            fees=[
                FeeLine(name, cell.billed_kobo, cell.paid_kobo, cell.left_kobo, cell.status)
                for name, cell in p.fees.items()
                if cell.billed_kobo > 0
            ],
            earlier_left_kobo=p.earlier_left_kobo,
        )
        for sid, p in pupils.items()
    }


FeeTargets = dict[str, list[tuple[uuid.UUID, uuid.UUID | None, str, int]]]


def fee_targets(session: Session, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, FeeTargets]:
    """
    Pupils' unpaid fees this term and where each sits: name -> [(bill, fee item, description, left)].
    A fixed number of queries for any number of pupils.
    """
    term_id = session.scalar(select(Term.id).where(Term.is_current.is_(True)))
    pupils = {sid: PupilStatus("", "", "") for sid in student_ids}
    _fill(session, term_id, pupils, describe_others=True)
    return {sid: p.targets for sid, p in pupils.items()}
