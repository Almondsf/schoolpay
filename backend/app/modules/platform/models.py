"""
The platform's own business: subscription plans and what schools pay for WardPay.

The model is that of ParentPay in the UK (PLAN.md §3.4): the school pays, parents use it free.
A plan has a one-off set-up fee, an annual licence, and a per-pupil fee per
year that is billed a third each term for the pupils enrolled that term.

Subscription invoices follow the same rules as school fee invoices: amounts in
kobo, lines are never edited (an invoice is voided and re-issued instead),
numbers are gapless, and what's paid comes from recorded payments.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.tenancy import TenantMixin


class Plan(UUIDPrimaryKey, Timestamped, Base):
    """Prices a school pays. Platform-wide (not a school's data); edited by platform admins."""

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "setup_fee_kobo >= 0 AND annual_fee_kobo >= 0 AND per_pupil_year_kobo >= 0", name="prices_not_negative"
        ),
        # Exactly one default plan (for schools without one of their own)
        Index("uq_plans_one_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )

    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    setup_fee_kobo: Mapped[int] = mapped_column(BigInteger)
    annual_fee_kobo: Mapped[int] = mapped_column(BigInteger)
    per_pupil_year_kobo: Mapped[int] = mapped_column(BigInteger)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SubscriptionLineKind(enum.StrEnum):
    SETUP = "setup"  # once, on a school's first bill
    LICENCE = "licence"  # once per school year (academic session)
    PUPILS = "pupils"  # a third of the yearly per-pupil fee, for the pupils enrolled that term


class SubscriptionInvoice(TenantMixin, UUIDPrimaryKey, Base):
    """What a school owes the platform for one term."""

    __tablename__ = "subscription_invoices"
    __table_args__ = (
        UniqueConstraint("number"),
        # One live bill per school per term: generating again can't double-bill
        Index(
            "uq_subscription_invoices_one_per_term",
            "school_id",
            "session_name",
            "term_number",
            unique=True,
            postgresql_where=text("voided_at IS NULL"),
        ),
        CheckConstraint("term_number BETWEEN 1 AND 3", name="term_range"),
        CheckConstraint("(voided_at IS NULL) = (void_reason IS NULL)", name="void_has_reason"),
    )

    number: Mapped[int] = mapped_column(BigInteger)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    session_name: Mapped[str] = mapped_column(String(20))  # "2026/2027"
    term_number: Mapped[int] = mapped_column(SmallInteger)
    pupils: Mapped[int] = mapped_column(Integer)  # enrolled when the bill was made
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_date: Mapped[date] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))


class SubscriptionLine(TenantMixin, UUIDPrimaryKey, Base):
    """One charge on a subscription invoice. Append-only (database trigger)."""

    __tablename__ = "subscription_lines"
    __table_args__ = (
        CheckConstraint("amount_kobo >= 0 AND quantity >= 0", name="not_negative"),
        Index("ix_subscription_lines_invoice", "invoice_id"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_invoices.id", ondelete="RESTRICT"))
    kind: Mapped[SubscriptionLineKind] = mapped_column(enum_type(SubscriptionLineKind, "subscription_line_kind"))
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_kobo: Mapped[int] = mapped_column(BigInteger)
    amount_kobo: Mapped[int] = mapped_column(BigInteger)


class SubscriptionPayment(TenantMixin, UUIDPrimaryKey, Base):
    """Money a school paid the platform (bank transfer to the platform's account). Append-only."""

    __tablename__ = "subscription_payments"
    __table_args__ = (
        CheckConstraint("amount_kobo > 0", name="amount_positive"),
        Index("ix_subscription_payments_invoice", "invoice_id"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_invoices.id", ondelete="RESTRICT"))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    received_on: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(100), default="")
    recorded_by: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
