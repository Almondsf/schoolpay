"""
The cash book: cash taken at the school, cash taken to the bank, and what should be in the drawer.

- **Cash in** is every cash payment as recorded (approved, or still waiting for its second person:
  either way the notes are in the drawer), less cash payments reversed, by the day it was received.
- **Cash out** is banking (`CashDeposit`): one person records a trip to the bank with its teller
  number, a second confirms it against the bank statement. A rejected one didn't happen, so the cash
  still counts as held; one still unchecked is shown apart, as "banked, not yet checked".
- **Closing a day** (`CashDayClose`): someone counts the drawer and enters the figure; the system's
  expected figure is kept beside it, and the difference shows. Cash recorded for a closed day after it
  was closed is listed as a late entry: that's where money goes missing.

Everything is a fixed number of queries whatever the range (owner rule: no database calls in loops).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, case, cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date

from app.core.clock import LAGOS, lagos_today
from app.core.errors import Conflict, DomainError, NotFound, PermissionDenied
from app.modules.accounts.deps import StaffContext
from app.modules.accounts.models import User
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record

from .models import (
    CashDayClose,
    CashDeposit,
    CashDepositStatus,
    ManualPayment,
    ManualPaymentStatus,
    Payment,
    PaymentKind,
    PaymentMethod,
)

MAX_DAYS = 366
_LAGOS_DAY = "Africa/Lagos"


@dataclass
class CashDay:
    day: date
    received_kobo: int = 0  # cash payments taken that day
    payments: int = 0
    reversed_kobo: int = 0  # cash payments reversed that day (a positive number)
    banked_kobo: int = 0  # deposits recorded for that day (confirmed or not yet checked)
    held_kobo: int = 0  # what should be in the drawer at the end of the day
    counted_kobo: int | None = None  # the count, if the day was closed
    difference_kobo: int | None = None  # counted - expected at close


@dataclass(frozen=True)
class StaffCash:
    user_id: uuid.UUID
    name: str
    received_kobo: int
    payments: int
    banked_kobo: int


@dataclass(frozen=True)
class LateEntry:
    kind: str  # "cash" | "banking"
    day: date
    amount_kobo: int
    recorded_at: datetime
    recorded_by: str


@dataclass
class CashBook:
    start: date
    end: date
    opening_kobo: int  # held at the start of `start`
    days: list[CashDay] = field(default_factory=list)
    staff: list[StaffCash] = field(default_factory=list)
    late: list[LateEntry] = field(default_factory=list)
    held_now_kobo: int = 0  # should be in the drawer right now
    unchecked_banking_kobo: int = 0  # banked but not yet confirmed against the bank statement
    last_closed: date | None = None


def _day(column: Any) -> ColumnElement[date]:
    """A timestamp's day in Lagos."""
    return cast(func.timezone(_LAGOS_DAY, column), Date)


def _cash_in(session: Session, until: date | None = None) -> int:
    """Cash taken up to and including `until` (all time if None), net of cash reversals. One query."""
    entries = select(func.coalesce(func.sum(ManualPayment.amount_kobo), 0)).where(
        ManualPayment.method == PaymentMethod.CASH,
        ManualPayment.status != ManualPaymentStatus.REJECTED,
        *([ManualPayment.received_on <= until] if until else []),
    )
    reversals = select(func.coalesce(func.sum(Payment.amount_kobo), 0)).where(
        Payment.kind == PaymentKind.REVERSAL,
        Payment.method == PaymentMethod.CASH,
        *([_day(Payment.received_at) <= until] if until else []),
    )
    return int(session.scalar(select(entries.scalar_subquery() + reversals.scalar_subquery())) or 0)


def _banked(session: Session, until: date | None = None) -> tuple[int, int]:
    """(banked confirmed or not yet checked, of which not yet checked), up to `until`. One query."""
    row = session.execute(
        select(
            func.coalesce(func.sum(CashDeposit.amount_kobo), 0),
            func.coalesce(
                func.sum(case((CashDeposit.status == CashDepositStatus.PENDING, CashDeposit.amount_kobo), else_=0)), 0
            ),
        ).where(
            CashDeposit.status != CashDepositStatus.REJECTED,
            *([CashDeposit.banked_on <= until] if until else []),
        )
    ).one()
    return int(row[0]), int(row[1])


def held(session: Session, until: date | None = None) -> int:
    """Cash that should be in the drawer at the end of `until` (or right now)."""
    return _cash_in(session, until) - _banked(session, until)[0]


def cash_book(session: Session, start: date, end: date) -> CashBook:
    """The cash book for [start, end]. The session is scoped to the school. Eight queries."""
    if end < start:
        raise DomainError("The end date is before the start date.", field="end")
    if (end - start).days >= MAX_DAYS:
        raise DomainError("Choose a year or less.", field="start")
    book = CashBook(start=start, end=end, opening_kobo=held(session, start - timedelta(days=1)))
    days: dict[date, CashDay] = {}

    def at(d: date) -> CashDay:
        return days.setdefault(d, CashDay(d))

    for d, amount, count in session.execute(
        select(ManualPayment.received_on, func.sum(ManualPayment.amount_kobo), func.count())
        .where(
            ManualPayment.method == PaymentMethod.CASH,
            ManualPayment.status != ManualPaymentStatus.REJECTED,
            ManualPayment.received_on.between(start, end),
        )
        .group_by(ManualPayment.received_on)
    ):
        at(d).received_kobo, at(d).payments = int(amount), int(count)
    reversal_day = _day(Payment.received_at)
    for d, amount in session.execute(
        select(reversal_day, func.sum(Payment.amount_kobo))
        .where(
            Payment.kind == PaymentKind.REVERSAL,
            Payment.method == PaymentMethod.CASH,
            reversal_day.between(start, end),
        )
        .group_by(reversal_day)
    ):
        at(d).reversed_kobo = -int(amount)
    for d, amount in session.execute(
        select(CashDeposit.banked_on, func.sum(CashDeposit.amount_kobo))
        .where(CashDeposit.status != CashDepositStatus.REJECTED, CashDeposit.banked_on.between(start, end))
        .group_by(CashDeposit.banked_on)
    ):
        at(d).banked_kobo = int(amount)
    closes = {c.day: c for c in session.scalars(select(CashDayClose).where(CashDayClose.day.between(start, end)))}
    for d in closes:
        at(d)
    running = book.opening_kobo
    for d in sorted(days):
        day = days[d]
        running += day.received_kobo - day.reversed_kobo - day.banked_kobo
        day.held_kobo = running
        close = closes.get(d)
        if close is not None:
            day.counted_kobo, day.difference_kobo = close.counted_kobo, close.counted_kobo - close.expected_kobo
        book.days.append(day)

    # Who took cash and who banked it, in the range
    names = User.full_name
    taken = {
        uid: (name, int(amount), int(count))
        for uid, name, amount, count in session.execute(
            select(ManualPayment.recorded_by, names, func.sum(ManualPayment.amount_kobo), func.count())
            .join(User, User.id == ManualPayment.recorded_by)
            .where(
                ManualPayment.method == PaymentMethod.CASH,
                ManualPayment.status != ManualPaymentStatus.REJECTED,
                ManualPayment.received_on.between(start, end),
            )
            .group_by(ManualPayment.recorded_by, names)
        )
    }
    banked_by = {
        uid: (name, int(amount))
        for uid, name, amount in session.execute(
            select(CashDeposit.recorded_by, names, func.sum(CashDeposit.amount_kobo))
            .join(User, User.id == CashDeposit.recorded_by)
            .where(CashDeposit.status != CashDepositStatus.REJECTED, CashDeposit.banked_on.between(start, end))
            .group_by(CashDeposit.recorded_by, names)
        )
    }
    book.staff = sorted(
        (
            StaffCash(
                uid,
                (taken.get(uid) or banked_by[uid])[0],
                taken.get(uid, ("", 0, 0))[1],
                taken.get(uid, ("", 0, 0))[2],
                banked_by.get(uid, ("", 0))[1],
            )
            for uid in {*taken, *banked_by}
        ),
        key=lambda s: (-s.received_kobo, s.name),
    )

    # Late entries: cash or banking recorded for a closed day after it was closed
    all_closes = select(CashDayClose.day, CashDayClose.closed_at).subquery()
    late_cash = (
        select(
            ManualPayment.received_on,
            ManualPayment.amount_kobo,
            ManualPayment.created_at,
            names,
        )
        .join(
            all_closes,
            and_(all_closes.c.day == ManualPayment.received_on, ManualPayment.created_at > all_closes.c.closed_at),
        )
        .join(User, User.id == ManualPayment.recorded_by)
        .where(
            ManualPayment.method == PaymentMethod.CASH,
            ManualPayment.status != ManualPaymentStatus.REJECTED,
            ManualPayment.received_on.between(start, end),
        )
    )
    late_banking = (
        select(CashDeposit.banked_on, CashDeposit.amount_kobo, CashDeposit.created_at, names)
        .join(
            all_closes, and_(all_closes.c.day == CashDeposit.banked_on, CashDeposit.created_at > all_closes.c.closed_at)
        )
        .join(User, User.id == CashDeposit.recorded_by)
        .where(CashDeposit.status != CashDepositStatus.REJECTED, CashDeposit.banked_on.between(start, end))
    )
    book.late = sorted(
        [LateEntry("cash", d, int(a), at_, n) for d, a, at_, n in session.execute(late_cash)]
        + [LateEntry("banking", d, int(a), at_, n) for d, a, at_, n in session.execute(late_banking)],
        key=lambda e: e.recorded_at,
        reverse=True,
    )

    cash_now, (banked_now, unchecked) = _cash_in(session), _banked(session)
    book.held_now_kobo, book.unchecked_banking_kobo = cash_now - banked_now, unchecked
    book.last_closed = session.scalar(select(func.max(CashDayClose.day)))
    return book


# --- Banking ----------------------------------------------------------------------------------------


def record_deposit(
    session: Session, ctx: StaffContext, *, amount_kobo: int, banked_on: date, bank_reference: str, note: str
) -> CashDeposit:
    if amount_kobo <= 0:
        raise DomainError("Enter the amount banked.", field="amount_kobo")
    if banked_on > lagos_today():
        raise DomainError("That date is in the future.", field="banked_on")
    deposit = CashDeposit(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        amount_kobo=amount_kobo,
        banked_on=banked_on,
        bank_reference=bank_reference.strip()[:100],
        note=note.strip()[:300],
        recorded_by=ctx.user.id,
    )
    session.add(deposit)
    record(
        session,
        AuditAction.CASH_DEPOSIT_RECORDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=deposit,
        changes={"amount_kobo": amount_kobo, "banked_on": banked_on, "bank_reference": deposit.bank_reference},
    )
    session.commit()
    return deposit


def decide_deposit(
    session: Session, ctx: StaffContext, deposit_id: uuid.UUID, *, confirm: bool, reason: str = ""
) -> CashDeposit:
    """A second person checks the banking against the bank statement: confirmed, or rejected with a reason."""
    deposit = session.scalar(select(CashDeposit).where(CashDeposit.id == deposit_id).with_for_update())
    if deposit is None:
        raise NotFound("Banking entry not found.")
    if deposit.status != CashDepositStatus.PENDING:
        raise Conflict(f"This banking entry was already {deposit.status.value}.")
    if deposit.recorded_by == ctx.user.id:
        raise PermissionDenied("Someone other than the person who recorded it must check it.", code="maker_checker")
    if not confirm and len(reason.strip()) < 3:
        raise DomainError("Say why it doesn't match the bank.", field="reason")
    deposit.status = CashDepositStatus.CONFIRMED if confirm else CashDepositStatus.REJECTED
    deposit.decided_by, deposit.decided_at = ctx.user.id, datetime.now(UTC)
    deposit.rejection_reason = "" if confirm else reason.strip()[:300]
    record(
        session,
        AuditAction.CASH_DEPOSIT_CONFIRMED if confirm else AuditAction.CASH_DEPOSIT_REJECTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=deposit,
        changes={"amount_kobo": deposit.amount_kobo, "banked_on": deposit.banked_on, "reason": reason.strip()[:300]},
    )
    session.commit()
    return deposit


def deposits(session: Session, *, status: CashDepositStatus | None = None, limit: int = 100) -> list[CashDeposit]:
    stmt = select(CashDeposit).order_by(CashDeposit.banked_on.desc(), CashDeposit.created_at.desc()).limit(limit)
    return list(session.scalars(stmt.where(CashDeposit.status == status) if status else stmt))


# --- Closing a day ----------------------------------------------------------------------------------


def close_day(session: Session, ctx: StaffContext, *, day: date, counted_kobo: int, note: str) -> CashDayClose:
    """Record the drawer count for a day next to what the system expected. Once per day, never changed."""
    if day > lagos_today():
        raise DomainError("That day hasn't happened yet.", field="day")
    if counted_kobo < 0:
        raise DomainError("Enter the cash counted.", field="counted_kobo")
    if session.scalar(select(CashDayClose.id).where(CashDayClose.day == day)) is not None:
        raise Conflict("That day is already closed.", field="day")
    expected = held(session, day)
    close = CashDayClose(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        day=day,
        expected_kobo=expected,
        counted_kobo=counted_kobo,
        note=note.strip()[:300],
        closed_by=ctx.user.id,
    )
    session.add(close)
    record(
        session,
        AuditAction.CASH_DAY_CLOSED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=close,
        changes={
            "day": day,
            "expected_kobo": expected,
            "counted_kobo": counted_kobo,
            "difference_kobo": counted_kobo - expected,
        },
    )
    session.commit()
    return close


def today_range(days: int = 30) -> tuple[date, date]:
    end = datetime.now(LAGOS).date()
    return end - timedelta(days=days - 1), end
