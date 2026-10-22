"""
The platform month by month (Lagos months), for the platform admin's trends charts:

- money parents paid schools through WardPay (net of reversals) and how many payments;
- subscription money schools paid the platform;
- schools that joined (confirmed; unconfirmed sign-ups don't count) and schools suspended.

Four grouped queries however many months or schools; months with nothing are zeros.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Date, case, cast, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.clock import LAGOS
from app.modules.audit.models import AuditAction, PlatformAuditLog
from app.modules.payments.models import Payment, PaymentKind
from app.modules.schools.models import School, SchoolStatus

from .models import SubscriptionPayment

MAX_MONTHS = 36


@dataclass(frozen=True)
class Month:
    month: str  # "2026-10"
    processed_kobo: int
    payments: int
    subscription_received_kobo: int
    schools_joined: int
    schools_suspended: int


def _months_back(today: date, months: int) -> list[date]:
    """The first day of each of the last `months` months, oldest first, ending with this one."""
    firsts, year, month = [], today.year, today.month
    for _ in range(months):
        firsts.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return firsts[::-1]


def _by_month(
    session: Session,
    moment: ColumnElement[Any] | InstrumentedAttribute[Any],
    *values: ColumnElement[Any],
    where: tuple[ColumnElement[bool], ...] = (),
) -> dict[date, tuple[Any, ...]]:
    """Each month's values, keyed by the month's first day."""
    month = cast(func.date_trunc("month", moment), Date)
    rows = session.execute(select(month, *values).where(*where).group_by(month)).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def trends(session: Session, months: int = 12, today: date | None = None) -> list[Month]:
    today = today or datetime.now(LAGOS).date()
    firsts = _months_back(today, max(1, min(months, MAX_MONTHS)))
    start = firsts[0]
    paid_at = func.timezone("Africa/Lagos", Payment.received_at)
    processed = _by_month(
        session,
        paid_at,
        func.coalesce(func.sum(Payment.amount_kobo), 0),
        func.count(case((Payment.kind == PaymentKind.RECEIPT, 1))),
        where=(paid_at >= start,),
    )
    subscriptions = _by_month(
        session,
        SubscriptionPayment.received_on,
        func.coalesce(func.sum(SubscriptionPayment.amount_kobo), 0),
        where=(SubscriptionPayment.received_on >= start,),
    )
    joined_at = func.timezone("Africa/Lagos", School.created_at)
    joined = _by_month(
        session, joined_at, func.count(), where=(joined_at >= start, School.status != SchoolStatus.PENDING)
    )
    suspended_at = func.timezone("Africa/Lagos", PlatformAuditLog.created_at)
    suspended = _by_month(
        session,
        suspended_at,
        func.count(),
        where=(suspended_at >= start, PlatformAuditLog.action == AuditAction.SCHOOL_SUSPENDED.value),
    )
    return [
        Month(
            month=f"{first:%Y-%m}",
            processed_kobo=int(processed.get(first, (0, 0))[0]),
            payments=int(processed.get(first, (0, 0))[1]),
            subscription_received_kobo=int(subscriptions.get(first, (0,))[0]),
            schools_joined=int(joined.get(first, (0,))[0]),
            schools_suspended=int(suspended.get(first, (0,))[0]),
        )
        for first in firsts
    ]
