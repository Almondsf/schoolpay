"""checkout payment methods

A payment made on the gateway's checkout page is recorded the way the parent paid:
card, bank transfer to a temporary account, or USSD.

Revision ID: 3f5191412a3b
Revises: 0a53a2c83873
Create Date: 2026-10-19 22:56:55.213838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f5191412a3b'
down_revision: Union[str, Sequence[str], None] = '0a53a2c83873'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Adding enum values is allowed in a transaction as long as this transaction doesn't use them
    op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'checkout_transfer' AFTER 'card'")
    op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'ussd' AFTER 'checkout_transfer'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres can't drop enum values, and payments recorded with them are append-only: nothing to undo
