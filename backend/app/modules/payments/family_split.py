"""
Sharing one amount between a family's children, by the rule the school chose (owner, 2026-10-23).

Used when a parent types one amount on the family payment link instead of choosing per child.
The rules, each a school setting (`School.family_split_rule`):

- **clear_most** (the default): bills from earlier terms (and one-off bills) first, oldest first,
  across all the children; then this term's bills, the child with the smallest balance first, so as
  many children as possible end up fully paid and none is sent home for a small balance.
- **proportional**: in proportion to what each child owes (largest remainder, to the kobo).
- **eldest_first** / **youngest_first**: one child at a time, by date of birth (or, where it's not
  recorded, by class: the highest class counts as the eldest).

Within each child the payment is applied as always: oldest bill first, tuition first. The amount
is never more than the family owes. Pure functions here; `load_debts` reads what's owed.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Invoice
from app.modules.schools.models import ClassLevel, FamilySplitRule, Term
from app.modules.students.models import Enrollment, Student


@dataclass(frozen=True)
class Bill:
    issued_at: datetime
    left_kobo: int
    this_term: bool


@dataclass(frozen=True)
class ChildDebt:
    student_id: uuid.UUID
    born: date | None = None
    class_rank: int | None = None
    bills: list[Bill] = field(default_factory=list)

    @property
    def owed_kobo(self) -> int:
        return sum(b.left_kobo for b in self.bills)

    @property
    def this_term_kobo(self) -> int:
        return sum(b.left_kobo for b in self.bills if b.this_term)


def _proportional(amount: int, children: list[ChildDebt]) -> dict[uuid.UUID, int]:
    owed = {c.student_id: c.owed_kobo for c in children}
    total = sum(owed.values())
    exact = {sid: amount * o / total for sid, o in owed.items()}
    shares = {sid: int(v) for sid, v in exact.items()}
    # Hand out the leftover kobo to the biggest remainders, never past what a child owes
    for sid in sorted(exact, key=lambda s: exact[s] - shares[s], reverse=True)[: amount - sum(shares.values())]:
        shares[sid] += 1
    return shares


def _age_key(child: ChildDebt) -> tuple[int, date | int]:
    """Smaller sorts older: a known birth date first (earliest = eldest), else the highest class."""
    if child.born is not None:
        return (0, child.born)
    return (1, -(child.class_rank or 0))


def split(amount_kobo: int, children: list[ChildDebt], rule: FamilySplitRule) -> dict[uuid.UUID, int]:
    """Each child's share of `amount_kobo` (capped at what the family owes). Children with nothing owed get 0."""
    owing = [c for c in children if c.owed_kobo > 0]
    shares = dict.fromkeys((c.student_id for c in children), 0)
    left = min(max(amount_kobo, 0), sum(c.owed_kobo for c in owing))
    if left == 0:
        return shares
    if rule is FamilySplitRule.PROPORTIONAL:
        shares.update(_proportional(left, owing))
        return shares

    def take(sid: uuid.UUID, wanted: int) -> None:
        nonlocal left
        given = min(wanted, left)
        shares[sid] += given
        left -= given

    if rule is FamilySplitRule.CLEAR_MOST:
        older = sorted(
            ((b.issued_at, c.student_id, b.left_kobo) for c in owing for b in c.bills if not b.this_term),
            key=lambda x: (x[0], str(x[1])),
        )
        for _, sid, kobo in older:
            take(sid, kobo)
        for child in sorted(owing, key=lambda c: (c.this_term_kobo, str(c.student_id))):
            take(child.student_id, child.this_term_kobo)
    else:
        ordered = sorted(owing, key=lambda c: (_age_key(c), str(c.student_id)))
        if rule is FamilySplitRule.YOUNGEST_FIRST:
            ordered.reverse()
        for child in ordered:
            take(child.student_id, child.owed_kobo)
    return shares


def load_debts(session: Session, student_ids: list[uuid.UUID]) -> list[ChildDebt]:
    """What each child owes, bill by bill, with birth date and current class. Two queries. Scoped session."""
    if not student_ids:
        return []
    totals, paid = totals_subquery(), paid_subquery()
    current = select(Term.id).where(Term.is_current.is_(True)).scalar_subquery()
    bills: dict[uuid.UUID, list[Bill]] = {sid: [] for sid in student_ids}
    for student_id, issued_at, term_id, is_current, total, done in session.execute(
        select(
            Invoice.student_id,
            Invoice.issued_at,
            Invoice.term_id,
            Invoice.term_id == current,
            totals.c.total_kobo,
            paid.c.paid_kobo,
        )
        .outerjoin(totals, totals.c.invoice_id == Invoice.id)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(Invoice.student_id.in_(student_ids), Invoice.voided_at.is_(None))
    ):
        left = int(total or 0) - int(done or 0)
        if left > 0:
            bills[student_id].append(Bill(issued_at, left, bool(term_id is not None and is_current)))
    people = {
        sid: (born, rank)
        for sid, born, rank in session.execute(
            select(Student.id, Student.date_of_birth, ClassLevel.rank)
            .select_from(Student)
            .outerjoin(
                Enrollment,
                and_(Enrollment.student_id == Student.id, Enrollment.term_id == current),
            )
            .outerjoin(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
            .where(Student.id.in_(student_ids))
        )
    }
    return [
        ChildDebt(sid, people.get(sid, (None, None))[0], people.get(sid, (None, None))[1], bills[sid])
        for sid in student_ids
    ]
