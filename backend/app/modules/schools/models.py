import enum

from sqlalchemy import CheckConstraint, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey


class SchoolStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"  # staff and parents can't sign in; data is kept


class School(UUIDPrimaryKey, Timestamped, Base):
    """The tenant. Everything a school owns hangs off this row via TenantMixin."""

    __tablename__ = "schools"
    __table_args__ = (CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),)

    name: Mapped[str] = mapped_column(String(200))
    # Short, URL-safe and unique: used in support lookups and receipt numbers
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    status: Mapped[SchoolStatus] = mapped_column(
        Enum(SchoolStatus, name="school_status", values_callable=lambda e: [m.value for m in e]),
        default=SchoolStatus.ACTIVE,
    )
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(255), default="")

    def __repr__(self) -> str:
        return f"<School {self.slug}>"
