"""
How much has been paid on each invoice: the one place balances get their
"paid" side. Every balance (invoice, student, term, debtors) goes through
`paid_subquery()`.
"""

from sqlalchemy import func, select
from sqlalchemy.sql import Subquery


def paid_subquery() -> Subquery:
    """invoice_id -> paid_kobo: the sum of payment allocations (reversals are negative allocations)."""
    from app.modules.payments.models import Allocation  # payments builds on fees, not the other way round

    return (
        select(Allocation.invoice_id.label("invoice_id"), func.sum(Allocation.amount_kobo).label("paid_kobo"))
        .group_by(Allocation.invoice_id)
        .subquery("invoice_paid")
    )
