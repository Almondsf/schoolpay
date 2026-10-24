"""
Settlement reconciliation: what the gateway actually paid into the school's bank, against what parents paid.

Once a day (and on request) WardPay asks the gateway for the school's payouts over the last weeks and the
transactions each one covered, and matches every transaction to a WardPay payment by the gateway's
reference. The report then shows, per payout, what parents paid, what came off before the bank (the
gateway's fees and any split), and what the bank received; plus:
- payments the gateway hasn't paid out a few days after they arrived (settlement is normally the next
  working day), and
- transactions in a payout that WardPay has no payment for.

The gateway's record is the source: a payout's rows are refreshed whenever it's fetched again (its
status moves from pending to success). Only gateways that can report payouts take part (Paystack;
the fake one in tests).

A sync is a few gateway calls and a fixed number of statements; the report is a fixed number of queries.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, cast, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import lagos_today
from app.core.errors import DomainError
from app.modules.receipts.models import Receipt
from app.modules.schools.models import School, SchoolStatus
from app.modules.students.models import Student

from .gateways import GatewayCode, enabled_gateways, get_gateway
from .gateways.base import SettlementSource
from .models import (
    GatewaySettlement,
    Payment,
    PaymentKind,
    PaymentMethod,
    SchoolPaymentAccount,
    SettlementItem,
)

LOOK_BACK_DAYS = 60  # the first sync
OVERLAP_DAYS = 7  # later syncs re-read the last week (pending payouts become paid)
UNSETTLED_AFTER_DAYS = 3
MAX_DAYS = 400
MAX_LISTED = 200


def _account(session: Session) -> tuple[SchoolPaymentAccount, SettlementSource] | None:
    enabled = enabled_gateways()
    for account in session.scalars(select(SchoolPaymentAccount).order_by(SchoolPaymentAccount.created_at)):
        if account.gateway not in enabled or not account.subaccount_code:
            continue
        gateway = get_gateway(account.gateway)
        if hasattr(gateway, "list_settlements") and hasattr(gateway, "settlement_transactions"):
            return account, gateway  # type: ignore[return-value]
    return None


@dataclass(frozen=True)
class SyncResult:
    settlements: int
    items: int
    matched: int


def sync(session: Session, school_id: uuid.UUID, today: date | None = None) -> SyncResult:
    """Fetch the school's recent payouts from the gateway and match their transactions to payments."""
    today = today or lagos_today()
    found = _account(session)
    if found is None:
        return SyncResult(0, 0, 0)
    account, gateway = found
    latest = session.scalar(
        select(func.max(GatewaySettlement.settled_on)).where(GatewaySettlement.gateway == account.gateway)
    )
    since = latest - timedelta(days=OVERLAP_DAYS) if latest else today - timedelta(days=LOOK_BACK_DAYS)
    # The gateway calls: one list, then one per payout (as the gateway requires)
    payouts = gateway.list_settlements(subaccount_code=account.subaccount_code, since=since)
    covered = {p.id: gateway.settlement_transactions(p.id) for p in payouts}
    now = datetime.now(UTC)
    session.execute(
        update(SchoolPaymentAccount).where(SchoolPaymentAccount.id == account.id).values(settlements_checked_at=now)
    )
    if not payouts:
        session.commit()
        return SyncResult(0, 0, 0)

    stmt = insert(GatewaySettlement).values(
        [
            {
                "id": uuid.uuid4(),
                "school_id": school_id,
                "gateway": account.gateway,
                "settlement_id": p.id,
                "status": p.status[:20],
                "settled_on": p.settled_on,
                "gross_kobo": max(p.gross_kobo, 0),
                "charges_kobo": p.charges_kobo,
                "paid_out_kobo": max(p.paid_out_kobo, 0),
            }
            for p in payouts
        ]
    )
    ids = dict(
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["school_id", "gateway", "settlement_id"],
                set_={
                    "status": stmt.excluded.status,
                    "settled_on": stmt.excluded.settled_on,
                    "gross_kobo": stmt.excluded.gross_kobo,
                    "charges_kobo": stmt.excluded.charges_kobo,
                    "paid_out_kobo": stmt.excluded.paid_out_kobo,
                    "updated_at": func.now(),
                },
            ).returning(GatewaySettlement.settlement_id, GatewaySettlement.id)
        ).all()
    )
    session.execute(delete(SettlementItem).where(SettlementItem.settlement_id.in_(ids.values())))
    references = {t.reference for items in covered.values() for t in items}
    payments = dict(
        session.execute(
            select(Payment.gateway_reference, Payment.id).where(
                Payment.gateway == account.gateway,
                Payment.kind == PaymentKind.RECEIPT,
                Payment.gateway_reference.in_(references),
            )
        ).all()
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "school_id": school_id,
            "settlement_id": ids[payout_id],
            "reference": t.reference[:100],
            "amount_kobo": t.amount_kobo,
            "fees_kobo": t.fees_kobo,
            "payment_id": payments.get(t.reference),
        }
        for payout_id, items in covered.items()
        for t in {i.reference: i for i in items}.values()  # a reference once per payout
    ]
    if rows:
        session.execute(insert(SettlementItem).values(rows))
    session.commit()
    return SyncResult(len(payouts), len(rows), sum(1 for r in rows if r["payment_id"]))


# --- The report -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PayoutRow:
    id: uuid.UUID
    settled_on: date | None
    status: str
    gross_kobo: int
    charges_kobo: int
    paid_out_kobo: int
    transactions: int
    matched: int


@dataclass(frozen=True)
class UnsettledPayment:
    payment_id: uuid.UUID
    received_at: datetime
    receipt_number: int | None
    admission_number: str
    method: PaymentMethod
    amount_kobo: int
    reference: str


@dataclass(frozen=True)
class UnknownItem:
    settled_on: date | None
    reference: str
    amount_kobo: int


@dataclass
class Reconciliation:
    start: date
    end: date
    connected: bool  # the school is on a gateway that reports payouts
    checked_at: datetime | None
    payouts: list[PayoutRow] = field(default_factory=list)
    unsettled: list[UnsettledPayment] = field(default_factory=list)
    unsettled_kobo: int = 0
    unknown: list[UnknownItem] = field(default_factory=list)

    @property
    def gross_kobo(self) -> int:
        return sum(p.gross_kobo for p in self.payouts)

    @property
    def charges_kobo(self) -> int:
        return sum(p.charges_kobo for p in self.payouts)

    @property
    def paid_out_kobo(self) -> int:
        return sum(p.paid_out_kobo for p in self.payouts)


def reconciliation(session: Session, start: date, end: date, today: date | None = None) -> Reconciliation:
    """Payouts settled in [start, end], and what's missing either way. Four queries."""
    today = today or lagos_today()
    if end < start:
        raise DomainError("The end date is before the start date.", field="end")
    if (end - start).days >= MAX_DAYS:
        raise DomainError("Choose a shorter period (a year or less).", field="start")
    found = _account(session)
    if found is None:
        return Reconciliation(start, end, connected=False, checked_at=None)
    account = found[0]
    report = Reconciliation(start, end, connected=True, checked_at=account.settlements_checked_at)

    report.payouts = [
        PayoutRow(sid, day, status, int(gross), int(charges), int(paid_out), int(n), int(matched))
        for sid, day, status, gross, charges, paid_out, n, matched in session.execute(
            select(
                GatewaySettlement.id,
                GatewaySettlement.settled_on,
                GatewaySettlement.status,
                GatewaySettlement.gross_kobo,
                GatewaySettlement.charges_kobo,
                GatewaySettlement.paid_out_kobo,
                func.count(SettlementItem.id),
                func.count(SettlementItem.payment_id),
            )
            .outerjoin(SettlementItem, SettlementItem.settlement_id == GatewaySettlement.id)
            .where(GatewaySettlement.gateway == account.gateway, GatewaySettlement.settled_on.between(start, end))
            .group_by(GatewaySettlement.id)
            .order_by(GatewaySettlement.settled_on.desc())
        )
    ]

    received = cast(func.timezone("Africa/Lagos", Payment.received_at), Date)
    cutoff = datetime.now(UTC) - timedelta(days=UNSETTLED_AFTER_DAYS)
    settled = exists().where(SettlementItem.payment_id == Payment.id)
    unsettled = (
        Payment.gateway == account.gateway,
        Payment.kind == PaymentKind.RECEIPT,
        received.between(start, end),
        Payment.received_at < cutoff,
        ~settled,
    )
    report.unsettled_kobo = int(
        session.scalar(select(func.coalesce(func.sum(Payment.amount_kobo), 0)).where(*unsettled)) or 0
    )
    report.unsettled = [
        UnsettledPayment(pid, at, number, admission, method, int(amount), reference or "")
        for pid, at, number, admission, method, amount, reference in session.execute(
            select(
                Payment.id,
                Payment.received_at,
                Receipt.number,
                Student.admission_number,
                Payment.method,
                Payment.amount_kobo,
                Payment.gateway_reference,
            )
            .join(Student, Student.id == Payment.student_id)
            .outerjoin(Receipt, Receipt.payment_id == Payment.id)
            .where(*unsettled)
            .order_by(Payment.received_at)
            .limit(MAX_LISTED)
        )
    ]
    report.unknown = [
        UnknownItem(day, reference, int(amount))
        for day, reference, amount in session.execute(
            select(GatewaySettlement.settled_on, SettlementItem.reference, SettlementItem.amount_kobo)
            .join(GatewaySettlement, GatewaySettlement.id == SettlementItem.settlement_id)
            .where(
                SettlementItem.payment_id.is_(None),
                GatewaySettlement.gateway == account.gateway,
                GatewaySettlement.settled_on.between(start, end),
            )
            .order_by(GatewaySettlement.settled_on, SettlementItem.reference)
            .limit(MAX_LISTED)
        )
    ]
    return report


def schools_to_sync(session: Session) -> list[uuid.UUID]:
    """Active schools on a gateway that reports payouts (platform-wide session). One query."""
    reporting = [c for c in enabled_gateways() if c in (GatewayCode.PAYSTACK, GatewayCode.FAKE)]
    return list(
        session.scalars(
            select(SchoolPaymentAccount.school_id)
            .join(School, School.id == SchoolPaymentAccount.school_id)
            .where(SchoolPaymentAccount.gateway.in_(reporting), School.status == SchoolStatus.ACTIVE)
            .distinct()
        )
    )


SYNC_TASK = "payments.sync_settlements"


def sync_school(school_id: uuid.UUID) -> SyncResult:
    """The background task for one school: its own scoped session."""
    from app.core.db import SessionLocal
    from app.core.tenancy import scope_to_school

    with SessionLocal() as session:
        scope_to_school(session, school_id)
        return sync(session, school_id)


def sync_all() -> int:
    """The daily job: one sync task per school that takes part, queued in one insert."""
    from app.core import outbox
    from app.core.db import SessionLocal
    from app.core.tenancy import scope_platform_wide

    with SessionLocal() as session:
        scope_platform_wide(session)
        school_ids = schools_to_sync(session)
        for school_id in school_ids:  # rows added to the session: one bulk insert on commit
            outbox.enqueue(session, SYNC_TASK, {"school_id": str(school_id)})
        session.commit()
    return len(school_ids)
