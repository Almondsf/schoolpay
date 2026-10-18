"""
How much has been paid on each invoice: the one place balances get their
"paid" side. Every balance (invoice, student, term, debtors) goes through
`paid_subquery()`, so payments only have to be wired in here.
"""

from sqlalchemy import false, literal, select
from sqlalchemy.sql import Subquery

from .models import InvoiceLine


def paid_subquery() -> Subquery:
    """invoice_id -> paid_kobo. No payments are recorded yet, so nothing is paid."""
    return (
        select(InvoiceLine.invoice_id.label("invoice_id"), literal(0).label("paid_kobo"))
        .where(false())
        .subquery("invoice_paid")
    )
