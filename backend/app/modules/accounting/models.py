"""
The school's own names and codes for the accounts in the journal export. WardPay has a default chart
(`journal.DEFAULT_ACCOUNTS`, plus one account per spending category); a school only stores the ones
its accountant wants called something else.
"""

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, Timestamped, UUIDPrimaryKey
from app.core.tenancy import TenantMixin


class JournalAccount(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "journal_accounts"
    __table_args__ = (UniqueConstraint("school_id", "key"),)

    # "bank", "fee:tuition", "expense:<category id>"...: see journal.account_keys
    key: Mapped[str] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
