"""
Fees and invoices.

Money is integer kobo everywhere (`BigInteger`); no floats.

The ledger: an `Invoice` is a set of `InvoiceLine`s, and its total is the sum
of its lines. Lines are append-only (a database trigger refuses UPDATE and
DELETE): a correction is a new `adjustment` line, never an edit. Discounts are
negative lines, so every naira on an invoice is traceable. A deferred
constraint trigger refuses any invoice whose lines sum below zero.

Balances are always computed from the ledger (lines minus payments); nothing
stores a balance that could drift.
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
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.tenancy import TenantMixin


class FeeCategory(enum.StrEnum):
    TUITION = "tuition"
    LEVY = "levy"  # PTA, development, sports...
    SERVICE = "service"  # bus, lunch, boarding
    MATERIALS = "materials"  # books, uniforms
    OTHER = "other"


class FeeItem(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """Something the school charges for, e.g. "Tuition", "School bus", "PTA levy"."""

    __tablename__ = "fee_items"
    __table_args__ = (UniqueConstraint("school_id", "name"),)

    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[FeeCategory] = mapped_column(enum_type(FeeCategory, "fee_category"), default=FeeCategory.OTHER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FeeSchedule(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """What one fee item costs for one class level in one term."""

    __tablename__ = "fee_schedules"
    __table_args__ = (
        UniqueConstraint("term_id", "class_level_id", "fee_item_id"),
        CheckConstraint("amount_kobo > 0", name="amount_positive"),
        Index("ix_fee_schedules_term", "term_id"),
    )

    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    class_level_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_levels.id", ondelete="RESTRICT"))
    fee_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fee_items.id", ondelete="RESTRICT"))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    # Optional items (bus, lunch) are only billed to students opted in for the term
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False)


class StudentFeeChoice(TenantMixin, Base):
    """A student opted in to an optional fee item for a term."""

    __tablename__ = "student_fee_choices"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"), primary_key=True)
    fee_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fee_items.id", ondelete="RESTRICT"), primary_key=True)


class DiscountKind(enum.StrEnum):
    PERCENT = "percent"  # value in basis points: 1000 = 10%
    FIXED = "fixed"  # value in kobo


class DiscountRule(enum.StrEnum):
    MANUAL = "manual"  # given to chosen students (scholarship, staff child)
    SIBLING = "sibling"  # automatic: every child after the eldest sharing a guardian


class Discount(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "discounts"
    __table_args__ = (
        UniqueConstraint("school_id", "name"),
        CheckConstraint("value > 0", name="value_positive"),
        CheckConstraint("kind <> 'percent' OR value <= 10000", name="percent_at_most_100"),
    )

    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[DiscountKind] = mapped_column(enum_type(DiscountKind, "discount_kind"))
    value: Mapped[int] = mapped_column(BigInteger)
    rule: Mapped[DiscountRule] = mapped_column(enum_type(DiscountRule, "discount_rule"), default=DiscountRule.MANUAL)
    # Only this item is discounted (e.g. tuition); None means every fee on the invoice
    fee_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_items.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StudentDiscount(TenantMixin, Base):
    """A manual discount given to a student, applied whenever their term invoices are generated."""

    __tablename__ = "student_discounts"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    discount_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discounts.id", ondelete="RESTRICT"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceKind(enum.StrEnum):
    TERM = "term"  # the term's fees: at most one live one per student per term
    OPENING_BALANCE = "opening_balance"  # what a student owed before the school joined SchoolPay
    ADHOC = "adhoc"  # one-off charges (replacement ID card, excursion)


class Invoice(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("school_id", "number"),
        Index(
            "uq_invoices_one_live_term_invoice",
            "student_id",
            "term_id",
            unique=True,
            postgresql_where=text("kind = 'term' AND voided_at IS NULL"),
        ),
        CheckConstraint("(kind = 'term') = (term_id IS NOT NULL)", name="term_matches_kind"),
        CheckConstraint("(voided_at IS NULL) = (void_reason IS NULL)", name="void_has_reason"),
        Index("ix_invoices_school_student", "school_id", "student_id"),
        Index("ix_invoices_term", "term_id"),
    )

    number: Mapped[int] = mapped_column(BigInteger)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    kind: Mapped[InvoiceKind] = mapped_column(enum_type(InvoiceKind, "invoice_kind"))
    term_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    # Class at billing time, kept for reports even if the student moves
    class_level_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("class_levels.id", ondelete="RESTRICT"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.position", lazy="raise"
    )


class LineKind(enum.StrEnum):
    FEE = "fee"  # positive
    DISCOUNT = "discount"  # negative
    ADJUSTMENT = "adjustment"  # a later correction, either sign


class InvoiceLine(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        CheckConstraint("kind <> 'fee' OR amount_kobo > 0", name="fee_positive"),
        CheckConstraint("kind <> 'discount' OR amount_kobo < 0", name="discount_negative"),
        CheckConstraint("kind <> 'adjustment' OR amount_kobo <> 0", name="adjustment_nonzero"),
        UniqueConstraint("invoice_id", "position"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[LineKind] = mapped_column(enum_type(LineKind, "invoice_line_kind"))
    description: Mapped[str] = mapped_column(String(200))
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    fee_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_items.id", ondelete="RESTRICT"))
    discount_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discounts.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    reason: Mapped[str | None] = mapped_column(String(300))

    invoice: Mapped[Invoice] = relationship(back_populates="lines", lazy="raise")
