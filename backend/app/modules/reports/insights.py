"""
Where collections are heading, for the proprietor's dashboard.

- **Collection curve**: of a term's bills, how much was collected by each day since the term began,
  for this term and the one before, so "by day 21 we're at 54%; last term we were at 61%" is one look.
  Money paid before the term began counts on day 0; for the last term, money paid after it ended
  counts on its last day.
- **Cash forecast** for the next 30, 60 and 90 days:
  - due: bills falling due in the window, and on a payment plan only the instalments that fall due
    (never the whole balance), less what's already been paid towards them;
  - already overdue;
  - money in and approved spending at the recent pace (the last 30 days), as a rough guide.

A fixed number of queries; the session is scoped to the school.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session

from app.modules.expenses.models import Expense, ExpenseStatus
from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Invoice, InvoiceKind, PaymentPlan, PlanInstalment
from app.modules.payments.models import Allocation, Payment
from app.modules.schools.models import AcademicSession, Term

from .service import _balances, _owed

WINDOWS = (30, 60, 90)
PACE_DAYS = 30
TERM_NAMES = ("First", "Second", "Third")


@dataclass
class TermCurve:
    term_id: uuid.UUID
    label: str
    start: date
    billed_kobo: int
    days: int  # how many days the curve covers (to today for the current term)
    collected_kobo: list[int] = field(default_factory=list)  # cumulative, one per day from day 0

    def at(self, day: int) -> int:
        if not self.collected_kobo:
            return 0
        return self.collected_kobo[min(day, len(self.collected_kobo) - 1)]


@dataclass(frozen=True)
class Window:
    days: int
    due_kobo: int  # falling due in the window (bills and plan instalments), still unpaid
    pace_in_kobo: int  # money in at the recent pace over the window
    pace_out_kobo: int  # approved spending at the recent pace over the window


@dataclass
class Insights:
    today: date
    current: TermCurve | None
    previous: TermCurve | None
    overdue_kobo: int
    windows: list[Window]


def _terms(session: Session, today: date) -> tuple[Term | None, Term | None, dict[uuid.UUID, str]]:
    terms = list(
        session.execute(
            select(Term, AcademicSession.name).join(AcademicSession, AcademicSession.id == Term.session_id)
        ).all()
    )
    labels = {t.id: f"{name} {TERM_NAMES[t.number - 1]} term" for t, name in terms}
    current = next((t for t, _ in terms if t.is_current), None)
    if current is None:
        return None, None, labels
    earlier = [t for t, _ in terms if t.start_date < current.start_date]
    previous = max(earlier, key=lambda t: t.start_date) if earlier else None
    return current, previous, labels


def _curves(
    session: Session, terms: list[Term], labels: dict[uuid.UUID, str], today: date
) -> dict[uuid.UUID, TermCurve]:
    """Billed per term and cumulative collections per day since its start. Two queries."""
    if not terms:
        return {}
    ids = [t.id for t in terms]
    totals = totals_subquery()
    billed: dict[uuid.UUID, int] = dict(
        session.execute(
            select(Invoice.term_id, func.coalesce(func.sum(totals.c.total_kobo), 0))
            .join(totals, totals.c.invoice_id == Invoice.id)
            .where(Invoice.term_id.in_(ids), Invoice.kind == InvoiceKind.TERM, Invoice.voided_at.is_(None))
            .group_by(Invoice.term_id)
        ).all()
    )
    curves = {
        t.id: TermCurve(
            t.id,
            labels[t.id],
            t.start_date,
            int(billed.get(t.id, 0)),
            max((min(today, t.end_date) - t.start_date).days, 0),
        )
        for t in terms
    }
    paid_on = cast(func.timezone("Africa/Lagos", Payment.received_at), Date)
    offset = paid_on - Term.start_date  # days since the term began (an integer in Postgres)
    span = Term.end_date - Term.start_date
    day = func.least(func.greatest(offset, 0), span)
    per_day: dict[uuid.UUID, dict[int, int]] = {t.id: {} for t in terms}
    for term_id, d, amount in session.execute(
        select(Invoice.term_id, day, func.sum(Allocation.amount_kobo))
        .join(Payment, Payment.id == Allocation.payment_id)
        .join(Invoice, Invoice.id == Allocation.invoice_id)
        .join(Term, Term.id == Invoice.term_id)
        .where(Invoice.term_id.in_(ids), Invoice.kind == InvoiceKind.TERM, Invoice.voided_at.is_(None))
        .group_by(Invoice.term_id, day)
    ):
        per_day[term_id][int(d)] = int(amount)
    for term_id, curve in curves.items():
        running = 0
        for d in range(curve.days + 1):
            running += per_day[term_id].get(d, 0)
            curve.collected_kobo.append(running)
    return curves


def _due(session: Session, today: date) -> tuple[int, dict[int, int]]:
    """(overdue now, {window: falling due in it}) over bills and plan instalments. Two queries."""
    balances = _balances(today)
    owed = _owed(balances.c.balance)
    plain = ~balances.c.on_plan
    row = session.execute(
        select(
            func.coalesce(func.sum(case((plain & (balances.c.due_on < today), owed), else_=0)), 0),
            *(
                func.coalesce(
                    func.sum(
                        case(
                            (plain & balances.c.due_on.between(today, today + timedelta(days=n)), owed),
                            else_=0,
                        )
                    ),
                    0,
                )
                for n in WINDOWS
            ),
        )
    ).one()
    overdue, due = int(row[0]), {n: int(v) for n, v in zip(WINDOWS, row[1:], strict=True)}

    # Plans: only what the instalments ask for, less what's been paid towards the plan
    paid = paid_subquery()
    paid_on_plan = func.greatest(func.coalesce(paid.c.paid_kobo, 0) - PaymentPlan.paid_at_start_kobo, 0)
    per_plan = (
        select(
            PaymentPlan.invoice_id.label("invoice_id"),
            func.max(paid_on_plan).label("paid"),
            func.sum(case((PlanInstalment.due_date < today, PlanInstalment.amount_kobo), else_=0)).label("before"),
            *(
                func.sum(
                    case((PlanInstalment.due_date <= today + timedelta(days=n), PlanInstalment.amount_kobo), else_=0)
                ).label(f"by_{n}")
                for n in WINDOWS
            ),
        )
        .join(PlanInstalment, PlanInstalment.plan_id == PaymentPlan.id)
        .join(Invoice, Invoice.id == PaymentPlan.invoice_id)
        .outerjoin(paid, paid.c.invoice_id == PaymentPlan.invoice_id)
        .where(PaymentPlan.cancelled_at.is_(None), Invoice.voided_at.is_(None))
        .group_by(PaymentPlan.invoice_id)
        .subquery("per_plan")
    )
    behind = func.greatest(per_plan.c.before - per_plan.c.paid, 0)
    plan_row = session.execute(
        select(
            func.coalesce(func.sum(behind), 0),
            *(
                func.coalesce(func.sum(func.greatest(per_plan.c[f"by_{n}"] - per_plan.c.paid, 0) - behind), 0)
                for n in WINDOWS
            ),
        )
    ).one()
    overdue += int(plan_row[0])
    for n, v in zip(WINDOWS, plan_row[1:], strict=True):
        due[n] += int(v)
    return overdue, due


def _pace(session: Session, today: date) -> tuple[int, int]:
    """Average money in and approved spending per day over the last PACE_DAYS. One query."""
    since = today - timedelta(days=PACE_DAYS - 1)
    paid_on = cast(func.timezone("Africa/Lagos", Payment.received_at), Date)
    money_in = select(func.coalesce(func.sum(Payment.amount_kobo), 0)).where(paid_on.between(since, today))
    money_out = select(func.coalesce(func.sum(Expense.amount_kobo), 0)).where(
        Expense.status == ExpenseStatus.APPROVED, Expense.spent_on.between(since, today)
    )
    row = session.execute(select(money_in.scalar_subquery(), money_out.scalar_subquery())).one()
    return int(row[0]) // PACE_DAYS, int(row[1]) // PACE_DAYS


def insights(session: Session, today: date) -> Insights:
    current, previous, labels = _terms(session, today)
    curves = _curves(session, [t for t in (current, previous) if t is not None], labels, today)
    overdue, due = _due(session, today)
    pace_in, pace_out = _pace(session, today)
    return Insights(
        today=today,
        current=curves.get(current.id) if current else None,
        previous=curves.get(previous.id) if previous else None,
        overdue_kobo=overdue,
        windows=[Window(n, due[n], pace_in * n, pace_out * n) for n in WINDOWS],
    )
