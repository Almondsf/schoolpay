"""
Receipts: one per payment, numbered per school with no gaps.

A receipt is issued in the same transaction as its payment and never changes
(append-only by trigger). It keeps a snapshot of what it says (school,
student, class, what was paid off, the balance after) so a reprint years later
reads exactly the same. Its status on the public verification page is derived
from the payment: a reversed payment shows its receipt as reversed.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, UUIDPrimaryKey
from app.core.tenancy import TenantMixin


class Receipt(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("school_id", "number"),
        UniqueConstraint("payment_id"),
        # The public verification link; unguessable so receipts can't be enumerated
        UniqueConstraint("verification_code"),
    )

    number: Mapped[int] = mapped_column(BigInteger)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    verification_code: Mapped[str] = mapped_column(String(32))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
