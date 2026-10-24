"""
Money the school spends (salaries, diesel, repairs...), recorded by one person and approved by another,
by category. Approved spending is never deleted: a mistake is voided with a reason. Spending paid from
the cash drawer also comes off what the cash book says should be held.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.tenancy import TenantMixin

# Created for every school the first time it looks at its categories; it can add, rename and retire its own
DEFAULT_CATEGORIES = (
    "Salaries and wages",
    "Diesel and fuel",
    "Electricity",
    "Repairs and maintenance",
    "Teaching materials",
    "Food and kitchen",
    "Transport",
    "Rent",
    "Taxes and levies",
    "Bank charges",
    "Other",
)


class ExpenseCategory(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "expense_categories"
    __table_args__ = (UniqueConstraint("school_id", "name"),)

    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaidFrom(enum.StrEnum):
    CASH = "cash"  # notes from the school's drawer: it comes off the cash book
    BANK = "bank"  # transfer or cheque from the school's account
    CARD = "card"  # the school's card or POS


class ExpenseStatus(enum.StrEnum):
    PENDING = "pending"  # recorded; a second person still has to approve it
    APPROVED = "approved"
    REJECTED = "rejected"  # wasn't right: doesn't count
    VOIDED = "voided"  # approved by mistake (wrong amount, recorded twice): doesn't count, kept on record


class Expense(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_kobo > 0", name="amount_positive"),
        CheckConstraint("(status = 'pending') = (decided_by IS NULL)", name="decided_unless_pending"),
        CheckConstraint("decided_by IS NULL OR decided_by <> recorded_by", name="maker_checker"),
        CheckConstraint("(status = 'voided') = (voided_by IS NOT NULL)", name="void_has_who"),
        Index("ix_expenses_school_day", "school_id", "spent_on"),
    )

    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("expense_categories.id", ondelete="RESTRICT"))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    spent_on: Mapped[date] = mapped_column(Date)
    payee: Mapped[str] = mapped_column(String(150))  # who was paid
    description: Mapped[str] = mapped_column(String(300), default="")
    paid_from: Mapped[PaidFrom] = mapped_column(enum_type(PaidFrom, "expense_paid_from"))
    reference: Mapped[str] = mapped_column(String(100), default="")  # receipt, invoice or transfer number
    status: Mapped[ExpenseStatus] = mapped_column(
        enum_type(ExpenseStatus, "expense_status"), default=ExpenseStatus.PENDING
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column()
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str] = mapped_column(String(300), default="")
    voided_by: Mapped[uuid.UUID | None] = mapped_column()
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str] = mapped_column(String(300), default="")
