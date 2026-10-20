"""
Paying for particular fees. A parent (on the payment page) or whoever records cash can say
what the money is for, e.g. WAEC. The payment is then earmarked: it goes to the bill holding
those fees first, even before older debts (owner's decision, 2026-10-20), and the fee-by-fee
views and the receipt show those fees paid. Transfers into a child's own account number
can't say what they're for, so they follow the bill order.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.money import format_naira
from app.modules.reports.fee_status import FeeTargets, fee_targets


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
