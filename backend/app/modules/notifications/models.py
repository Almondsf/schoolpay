"""
Messages to parents: what was sent (or skipped, and why), each school's
wording, and its reminder schedule.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.tenancy import TenantMixin


class MessageKind(enum.StrEnum):
    RECEIPT = "receipt"  # "we received your payment" (transactional: sent even to opted-out parents)
    FEE_NOTICE = "fee_notice"  # a new bill
    REMINDER = "reminder"  # before or after the due date
    CALL_REMINDER = "call_reminder"  # a phone call about an overdue bill, beside the text reminder
    CALL_RECEIPT = "call_receipt"  # a phone call confirming a payment, beside the text receipt


class Channel(enum.StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"
    VOICE = "voice"


class MessageStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"  # every channel failed, after retries
    SKIPPED = "skipped"  # opted out, no contact details, too old, or the debt was paid meanwhile


class MessageLog(TenantMixin, UUIDPrimaryKey, Base):
    """
    Every message to a parent, one row per message. `dedupe_key` makes sending
    idempotent: retries and repeated triggers can't message a parent twice.
    """

    __tablename__ = "message_log"
    __table_args__ = (
        UniqueConstraint("school_id", "dedupe_key"),
        Index("ix_message_log_school_created", "school_id", "created_at"),
        Index("ix_message_log_queued", "created_at", postgresql_where=text("status = 'queued'")),
        Index("ix_message_log_invoice", "invoice_id"),
    )

    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"))
    kind: Mapped[MessageKind] = mapped_column(enum_type(MessageKind, "message_kind"))
    dedupe_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[MessageStatus] = mapped_column(
        enum_type(MessageStatus, "message_status"), default=MessageStatus.QUEUED
    )
    channel: Mapped[Channel | None] = mapped_column(enum_type(Channel, "message_channel"))
    body: Mapped[str] = mapped_column(Text, default="")
    skip_reason: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider_message_id: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageTemplate(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """A school's own wording for SMS and email. Without one, the default wording is used."""

    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("school_id", "kind"),)

    kind: Mapped[MessageKind] = mapped_column(enum_type(MessageKind, "message_kind"))
    body: Mapped[str] = mapped_column(Text)


class ReminderSettings(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """When a school's fee reminders go out. One row per school; defaults apply until it's saved."""

    __tablename__ = "reminder_settings"
    __table_args__ = (
        UniqueConstraint("school_id"),
        CheckConstraint("send_hour BETWEEN 7 AND 19", name="daytime_only"),
        CheckConstraint("max_per_invoice BETWEEN 0 AND 10", name="max_range"),
        CheckConstraint("min_gap_days BETWEEN 1 AND 30", name="gap_range"),
        CheckConstraint("max_calls_per_invoice BETWEEN 0 AND 5", name="max_calls_range"),
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    days_before_due: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=lambda: [7, 1])
    days_after_due: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=lambda: [3, 14, 30])
    # Lagos time; reminders never go out at night
    send_hour: Mapped[int] = mapped_column(SmallInteger, default=9)
    max_per_invoice: Mapped[int] = mapped_column(SmallInteger, default=4)
    min_gap_days: Mapped[int] = mapped_column(SmallInteger, default=3)
    # Phone calls to parents who prefer them (they cost the school per minute, so off until switched on)
    calls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    max_calls_per_invoice: Mapped[int] = mapped_column(SmallInteger, default=2, server_default="2")
