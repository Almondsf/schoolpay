"""
Parent sign-in: one-time codes and portal sessions.

Parents are guardians (see students.models), found by the blind index of their
phone. A parent with children at two schools is a guardian in each, so a code
is issued per phone (platform-level), and each portal session is bound to one
guardian in one school.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, UUIDPrimaryKey
from app.core.crypto import BLIND_INDEX_TYPE


class ParentOtp(UUIDPrimaryKey, Base):
    """A sign-in code. Only a keyed hash is stored; it expires quickly and allows a few attempts."""

    __tablename__ = "parent_otps"
    __table_args__ = (Index("ix_parent_otps_live", "phone_index", postgresql_where=text("consumed_at IS NULL")),)

    phone_index: Mapped[str] = mapped_column(BLIND_INDEX_TYPE)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParentSession(UUIDPrimaryKey, Base):
    """
    A signed-in parent device, bound to one guardian record (so one school).
    Not a TenantMixin table: it's looked up before the school is known, like
    staff sessions. The refresh token rotates; presenting the previous one again
    (a copied cookie) ends the session.
    """

    __tablename__ = "parent_sessions"
    __table_args__ = (
        Index("ix_parent_sessions_guardian_active", "guardian_id", postgresql_where=text("revoked_at IS NULL")),
    )

    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"))
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"))
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_refresh_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str] = mapped_column(String(50), default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
