"""
Closing the books at the end of a term, so figures that have been reported never change behind anyone's back.

Closing a term sets the school's `books_closed_through` to the term's last day. From then on nothing dated
on or before that day can be recorded: cash, teller, POS and bank-transfer payments, spending, banking,
the drawer count; and the closed term's bills can't be generated again or voided. Corrections still
happen, dated today, so they land in the current period: adjusting a bill, voiding spending, reversing a
payment. Money that really arrives through the payment provider is never refused.

Before closing, everything awaiting a second person's decision in the period must be decided. The term's
figures at that moment are kept with the closing. Only the proprietor can re-open, with a reason, which
moves the date back to the end of the term before. Every close and re-open is in the audit log.

Every recording path calls `ensure_open`, which reads the school row FOR SHARE: a close (FOR UPDATE)
waits for recordings in flight, and recordings wait for a close in flight.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import lagos_today
from app.core.errors import Conflict, DomainError
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.expenses.models import Expense, ExpenseStatus
from app.modules.payments.models import CashDeposit, CashDepositStatus, ManualPayment, ManualPaymentStatus
from app.modules.schools.models import School, Term

from .models import BookAction, BookClosing


def _closed_through(session: Session, school_id: uuid.UUID, *, lock: str) -> date | None:
    stmt = select(School.books_closed_through).where(School.id == school_id)
    stmt = stmt.with_for_update() if lock == "update" else stmt.with_for_update(read=True)
    return session.scalar(stmt)


def ensure_open(session: Session, school_id: uuid.UUID, day: date, *, field: str | None = None) -> None:
    """Refuse anything dated on or before the day the books are closed through. One query."""
    closed = _closed_through(session, school_id, lock="share")
    if closed is not None and day <= closed:
        raise DomainError(
            f"The books are closed up to {closed:%d %b %Y}. Record it with a later date, "
            "or ask the proprietor to re-open the term.",
            field=field,
            code="books_closed",
        )


def closed_on(session: Session, school_id: uuid.UUID, day: date) -> bool:
    closed = _closed_through(session, school_id, lock="share")
    return closed is not None and day <= closed


def term_is_closed(session: Session, school_id: uuid.UUID, term: Term) -> bool:
    return closed_on(session, school_id, term.end_date)


@dataclass(frozen=True)
class Waiting:
    payments: int
    spending: int
    banking: int

    def __bool__(self) -> bool:
        return bool(self.payments or self.spending or self.banking)


def waiting(session: Session, through: date) -> Waiting:
    """What's still awaiting a second person's decision on or before `through`. One query."""
    row = session.execute(
        select(
            select(func.count())
            .where(ManualPayment.status == ManualPaymentStatus.PENDING, ManualPayment.received_on <= through)
            .scalar_subquery(),
            select(func.count())
            .where(Expense.status == ExpenseStatus.PENDING, Expense.spent_on <= through)
            .scalar_subquery(),
            select(func.count())
            .where(CashDeposit.status == CashDepositStatus.PENDING, CashDeposit.banked_on <= through)
            .scalar_subquery(),
        )
    ).one()
    return Waiting(int(row[0]), int(row[1]), int(row[2]))


def close_term(session: Session, ctx: StaffContext, term_id: uuid.UUID, today: date | None = None) -> BookClosing:
    from app.modules.reports.service import overview  # here: payments and fees import this module

    today = today or lagos_today()
    term = get_or_404(session, Term, term_id, label="Term")
    closed = _closed_through(session, ctx.school_id, lock="update")
    if closed is not None and term.end_date <= closed:
        raise Conflict("That term is already closed.")
    if term.end_date >= today:
        raise DomainError(f"The term runs to {term.end_date:%d %b %Y}. Close it after it has ended.")
    pending = waiting(session, term.end_date)
    if pending:
        parts = [
            f"{n} {what}"
            for n, what in (
                (pending.payments, "cash or teller payments"),
                (pending.spending, "spending entries"),
                (pending.banking, "banking entries"),
            )
            if n
        ]
        raise Conflict(
            f"Still awaiting a second person's decision up to {term.end_date:%d %b %Y}: {', '.join(parts)}. "
            "Approve or reject them first.",
            code="books_waiting",
        )
    figures = overview(session, term.id, today)
    snapshot = {
        "term": figures.term_label,
        "invoices": figures.invoices,
        "billed_kobo": figures.billed_kobo,
        "collected_kobo": figures.collected_kobo,
        "outstanding_kobo": figures.outstanding_kobo,
        "students_owing": figures.students_owing,
        "credit_kobo": figures.credit_kobo,
    }
    session.execute(update(School).where(School.id == ctx.school_id).values(books_closed_through=term.end_date))
    entry = BookClosing(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        action=BookAction.CLOSE,
        term_id=term.id,
        closed_through_before=closed,
        closed_through_after=term.end_date,
        figures=snapshot,
        by=ctx.user.id,
    )
    session.add(entry)
    record(
        session,
        AuditAction.BOOKS_CLOSED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=entry,
        changes={"closed_through": [closed, term.end_date], **snapshot},
    )
    session.commit()
    return entry


def reopen(session: Session, ctx: StaffContext, reason: str) -> BookClosing:
    """Re-open the last closed term: the books go back to the end of the term before it."""
    reason = reason.strip()
    if len(reason) < 3:
        raise DomainError("Say why the term is being re-opened.", field="reason")
    closed = _closed_through(session, ctx.school_id, lock="update")
    if closed is None:
        raise Conflict("No term is closed.")
    # The term being re-opened is the one ending on that day (closed terms' dates can't change)
    term_id = session.scalar(select(Term.id).where(Term.end_date == closed).order_by(Term.start_date.desc()).limit(1))
    if term_id is None:
        raise Conflict("No term is closed.")
    earlier = session.scalar(select(func.max(Term.end_date)).where(Term.end_date < closed))
    session.execute(update(School).where(School.id == ctx.school_id).values(books_closed_through=earlier))
    entry = BookClosing(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        action=BookAction.REOPEN,
        term_id=term_id,
        closed_through_before=closed,
        closed_through_after=earlier,
        reason=reason[:300],
        by=ctx.user.id,
    )
    session.add(entry)
    record(
        session,
        AuditAction.BOOKS_REOPENED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=entry,
        changes={"closed_through": [closed, earlier], "reason": reason[:300]},
    )
    session.commit()
    return entry


def history(session: Session, limit: int = 50) -> list[BookClosing]:
    return list(session.scalars(select(BookClosing).order_by(BookClosing.created_at.desc()).limit(limit)))
