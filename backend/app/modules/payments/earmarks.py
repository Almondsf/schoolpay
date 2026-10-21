"""
Paying for particular fees. A parent (on the payment page) or whoever records cash can say
what the money is for, e.g. WAEC. The payment is then earmarked: it goes to the bill holding
those fees first, even before older debts (owner's decision, 2026-10-20), and the fee-by-fee
views and the receipt show those fees paid. Transfers into a child's own account number
can't say what they're for, so they cover tuition first, then the bill's other fees in order.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.core.money import format_naira
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.fees.models import Discount, FeeItem, InvoiceLine
from app.modules.reports.fee_status import FeeTargets, fee_targets

from .models import Allocation, Payment, PaymentEarmark, PaymentKind


@dataclass(frozen=True)
class Earmark:
    invoice_id: uuid.UUID
    fee_item_id: uuid.UUID | None
    description: str  # for a one-off charge without a fee item
    amount_kobo: int
    name: str  # as the payer saw it, for the receipt

    def to_json(self) -> dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "fee_item_id": str(self.fee_item_id) if self.fee_item_id else None,
            "description": self.description,
            "amount_kobo": self.amount_kobo,
            "name": self.name,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Earmark":
        return cls(
            invoice_id=uuid.UUID(data["invoice_id"]),
            fee_item_id=uuid.UUID(data["fee_item_id"]) if data.get("fee_item_id") else None,
            description=str(data.get("description", "")),
            amount_kobo=int(data["amount_kobo"]),
            name=str(data.get("name", "")),
        )


def choose(
    session: Session, student_id: uuid.UUID, fee_names: list[str], amount_kobo: int | None = None
) -> tuple[list[Earmark], int]:
    """Earmarks for one pupil (see `choose_from`)."""
    return choose_from(fee_targets(session, [student_id])[student_id], fee_names, amount_kobo)


def choose_from(targets: FeeTargets, fee_names: list[str], amount_kobo: int | None = None) -> tuple[list[Earmark], int]:
    """
    Earmarks for paying these fees this term, and the amount: by default everything left on them;
    less covers them in the order given. No queries (the targets are looked up in one go).
    """
    names = list(dict.fromkeys(n.strip() for n in fee_names if n.strip()))
    if not names:
        raise DomainError("Choose the fees to pay for.", field="fees")
    missing = [n for n in names if n not in targets]
    if missing:
        raise DomainError(f"{', '.join(missing)}: nothing is owed on that this term.", field="fees")
    left = sum(t[3] for n in names for t in targets[n])
    amount = left if amount_kobo is None else amount_kobo
    if not 0 < amount <= left:
        raise DomainError(f"For those fees, pay up to {format_naira(left)}.", field="amount_kobo")
    earmarks: list[Earmark] = []
    to_place = amount
    for name in names:
        for invoice_id, fee_item_id, description, owed in targets[name]:
            if to_place == 0:
                break
            share = min(owed, to_place)
            earmarks.append(Earmark(invoice_id, fee_item_id, description, share, name))
            to_place -= share
    return earmarks, amount


# --- Marking a payment after it arrived -----------------------------------------------------------


@dataclass(frozen=True)
class Marked:
    name: str
    amount_kobo: int


def marked_fees(session: Session, payment_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """The fees each payment was marked for (empty: tuition first, then the bill's other fees). One query."""
    result: dict[uuid.UUID, list[str]] = {pid: [] for pid in payment_ids}
    if not payment_ids:
        return result
    for payment_id, fee_name, description in session.execute(
        select(PaymentEarmark.payment_id, FeeItem.name, PaymentEarmark.description)
        .outerjoin(FeeItem, FeeItem.id == PaymentEarmark.fee_item_id)
        .where(PaymentEarmark.payment_id.in_(payment_ids))
        .order_by(PaymentEarmark.payment_id, PaymentEarmark.id)
    ):
        name = fee_name or description
        if name and name not in result[payment_id]:
            result[payment_id].append(name)
    return result


def mark_payment(session: Session, ctx: StaffContext, payment_id: uuid.UUID, fee_names: list[str]) -> list[Marked]:
    """
    Say what a payment was for after it arrived (e.g. a parent says their transfer was for the PTA
    levy). The money it put on each bill is placed against those fees, in the order given, up to
    what each fee is still open for; bill totals and balances don't change, only which fees show
    as paid. Marks are permanent, so a payment is marked once, and only if it wasn't marked when
    it came in. Audited. A fixed number of statements.
    """
    names = list(dict.fromkeys(n.strip() for n in fee_names if n.strip()))
    if not names:
        raise DomainError("Choose the fees this payment was for.", field="fees")
    payment = get_or_404(session, Payment, payment_id, label="Payment", lock=True)
    if payment.kind != PaymentKind.RECEIPT:
        raise DomainError("A reversal can't be marked for fees.")
    reversed_ = session.scalar(select(Payment.id).where(Payment.reverses_payment_id == payment.id))
    if reversed_ is not None:
        raise DomainError("This payment was reversed.")
    if session.scalar(select(PaymentEarmark.id).where(PaymentEarmark.payment_id == payment.id).limit(1)):
        raise Conflict("This payment is already marked for fees, and that can't be changed.", field="fees")

    gave = {
        invoice_id: int(amount)
        for invoice_id, amount in session.execute(
            select(Allocation.invoice_id, func.sum(Allocation.amount_kobo))
            .where(Allocation.payment_id == payment.id)
            .group_by(Allocation.invoice_id)
        )
        if amount > 0
    }
    if not gave:
        raise DomainError("This payment isn't on any bill yet (it's held as credit).")

    # Each bill's fees: what they come to, less what other payments were already marked for
    fee_of_line = func.coalesce(InvoiceLine.fee_item_id, Discount.fee_item_id)
    open_: dict[tuple[uuid.UUID, str], int] = {}
    ref: dict[tuple[uuid.UUID, str], tuple[uuid.UUID | None, str]] = {}
    for invoice_id, item_id, item_name, description, amount in session.execute(
        select(InvoiceLine.invoice_id, fee_of_line, FeeItem.name, InvoiceLine.description, InvoiceLine.amount_kobo)
        .outerjoin(Discount, Discount.id == InvoiceLine.discount_id)
        .outerjoin(FeeItem, FeeItem.id == fee_of_line)
        .where(InvoiceLine.invoice_id.in_(list(gave)))
        .order_by(InvoiceLine.invoice_id, InvoiceLine.position)
    ):
        name = item_name if item_id is not None else description
        if not name:
            continue
        open_[(invoice_id, name)] = open_.get((invoice_id, name), 0) + int(amount)
        ref.setdefault((invoice_id, name), (item_id, "" if item_id else description))
    undone = select(Payment.reverses_payment_id).where(Payment.reverses_payment_id.is_not(None))
    for invoice_id, item_name, description, amount in session.execute(
        select(
            PaymentEarmark.invoice_id, FeeItem.name, PaymentEarmark.description, func.sum(PaymentEarmark.amount_kobo)
        )
        .outerjoin(FeeItem, FeeItem.id == PaymentEarmark.fee_item_id)
        .where(PaymentEarmark.invoice_id.in_(list(gave)), PaymentEarmark.payment_id.not_in(undone))
        .group_by(PaymentEarmark.invoice_id, FeeItem.name, PaymentEarmark.description)
    ):
        key = (invoice_id, item_name or description)
        if key in open_:
            open_[key] -= int(amount)

    on_bills = {name for _, name in open_}
    unknown = [n for n in names if n not in on_bills]
    if unknown:
        raise DomainError(f"{', '.join(unknown)}: not on the bills this payment went to.", field="fees")
    marks: list[PaymentEarmark] = []
    placed: dict[str, int] = {}
    for name in names:
        for invoice_id in gave:
            share = min(max(open_.get((invoice_id, name), 0), 0), gave[invoice_id])
            if share <= 0:
                continue
            mark_item, text = ref[(invoice_id, name)]
            marks.append(
                PaymentEarmark(
                    payment_id=payment.id,
                    invoice_id=invoice_id,
                    fee_item_id=mark_item,
                    description=text,
                    amount_kobo=share,
                )
            )
            gave[invoice_id] -= share
            open_[(invoice_id, name)] -= share
            placed[name] = placed.get(name, 0) + share
    if not marks:
        raise DomainError("Those fees are already covered by other payments marked for them.", field="fees")
    session.add_all(marks)
    record(
        session,
        AuditAction.PAYMENT_MARKED_FOR_FEES,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=payment,
        changes={"for": [{"fee": n, "amount_kobo": a} for n, a in placed.items()]},
    )
    session.commit()
    return [Marked(n, a) for n, a in placed.items()]
