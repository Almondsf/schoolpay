"""
Closing the books (accounting/periods.py), and the school's own names and codes for the accounts in
the journal export. WardPay has a default chart
(`journal.DEFAULT_ACCOUNTS`, plus one account per spending category); a school only stores the ones
its accountant wants called something else.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.tenancy import TenantMixin


class JournalAccount(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "journal_accounts"
    __table_args__ = (UniqueConstraint("school_id", "key"),)

    # "bank", "fee:tuition", "expense:<category id>"...: see journal.account_keys
    key: Mapped[str] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))


class BookAction(enum.StrEnum):
    CLOSE = "close"
    REOPEN = "reopen"


class BookClosing(TenantMixin, UUIDPrimaryKey, Base):
    """
    Each time a school closed or re-opened its books, with the term's figures at that moment.
    Never changed or deleted (trigger).
    """

    __tablename__ = "book_closings"
    __table_args__ = (
        CheckConstraint("action = 'close' OR length(reason) >= 3", name="reopen_has_reason"),
        Index("ix_book_closings_school_at", "school_id", "created_at"),
    )

    action: Mapped[BookAction] = mapped_column(enum_type(BookAction, "book_action"))
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    closed_through_before: Mapped[date | None] = mapped_column(Date)
    closed_through_after: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(300), default="")
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # the term's figures when closed
    by: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportKind(enum.StrEnum):
    JOURNAL = "journal"
    SESSION_PACK = "session_pack"


class ExportStatus(enum.StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ExportFile(TenantMixin, UUIDPrimaryKey, Base):
    """
    A download built in the background (accounting/export_jobs.py), so a big school's file never times out
    in the browser. Kept for a day, then deleted by the daily retention job. Only the person who asked can
    download it.
    """

    __tablename__ = "export_files"
    __table_args__ = (
        CheckConstraint("(status = 'ready') = (content IS NOT NULL)", name="ready_has_content"),
        Index("ix_export_files_expires", "expires_at"),
    )

    kind: Mapped[ExportKind] = mapped_column(enum_type(ExportKind, "export_kind"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[ExportStatus] = mapped_column(enum_type(ExportStatus, "export_status"), default=ExportStatus.PENDING)
    filename: Mapped[str] = mapped_column(String(150), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(300), default="")
    requested_by: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
