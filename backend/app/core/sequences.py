"""
Gapless per-school numbering (invoices, receipts).

`reserve(session, school_id, "invoice", n)` returns the next n numbers. The
counter row stays locked until the caller's transaction ends, so concurrent
callers queue up, and a rollback rolls the counter back too: numbers are
never skipped or reused.
"""

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.base import Base


class NumberSequence(Base):
    __tablename__ = "number_sequences"

    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schools.id", ondelete="RESTRICT"), primary_key=True)
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    # The last number handed out
    last_value: Mapped[int] = mapped_column(BigInteger, default=0)


def reserve(session: Session, school_id: uuid.UUID, name: str, count: int = 1) -> range:
    """The next `count` numbers for (school, name), as a range. One statement whatever `count` is."""
    if count < 1:
        return range(0)
    stmt = (
        insert(NumberSequence)
        .values(school_id=school_id, name=name, last_value=count)
        .on_conflict_do_update(
            index_elements=["school_id", "name"], set_={"last_value": NumberSequence.last_value + count}
        )
        .returning(NumberSequence.last_value)
    )
    last = session.execute(stmt).scalar_one()
    return range(last - count + 1, last + 1)


class PlatformSequence(Base):
    """Gapless numbering that isn't per school (the platform's own invoices to schools)."""

    __tablename__ = "platform_sequences"

    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, default=0)


def reserve_platform(session: Session, name: str, count: int = 1) -> range:
    """Like `reserve`, for platform-wide numbers. Locked until the caller's transaction ends."""
    if count < 1:
        return range(0)
    stmt = (
        insert(PlatformSequence)
        .values(name=name, last_value=count)
        .on_conflict_do_update(index_elements=["name"], set_={"last_value": PlatformSequence.last_value + count})
        .returning(PlatformSequence.last_value)
    )
    last = session.execute(stmt).scalar_one()
    return range(last - count + 1, last + 1)
