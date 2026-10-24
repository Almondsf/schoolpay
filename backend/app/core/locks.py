"""
Transaction-scoped Postgres advisory locks, for work that reads "what's there" and then writes based
on it, where two people pressing the same button at once would otherwise both act on the same stale
read (billing a term twice, adding a fee to the same bills twice). The second caller waits, then
reads what the first one did. The lock is released when the transaction commits or rolls back.

    hold(session, "billing", school_id)
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def hold(session: Session, scope: str, owner: uuid.UUID | str = "") -> None:
    """Wait for, then hold until this transaction ends, the lock for (scope, owner)."""
    key = func.hashtextextended(f"{scope}:{owner}", 0)
    session.execute(select(func.pg_advisory_xact_lock(key)))
