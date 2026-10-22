"""subscription discounts

A platform admin can take money off a subscription bill (e.g. waive the set-up fee): a new
"discount" line, the only kind allowed to be negative. Lines stay append-only.

Revision ID: 4d5c5b8e17cd
Revises: 4bb10deb5185
Create Date: 2026-10-23 00:27:15.455012

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4d5c5b8e17cd'
down_revision: Union[str, Sequence[str], None] = '4bb10deb5185'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A new enum value can't be used in the transaction that adds it
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE subscription_line_kind ADD VALUE IF NOT EXISTS 'discount'")
    op.drop_constraint(op.f('ck_subscription_lines_not_negative'), 'subscription_lines', type_='check')
    op.create_check_constraint(
        op.f('ck_subscription_lines_amount_sign'),
        'subscription_lines',
        "quantity >= 0 AND CASE WHEN kind = 'discount' THEN amount_kobo < 0 ELSE amount_kobo >= 0 END",
    )


def downgrade() -> None:
    """Downgrade schema. (Postgres can't drop an enum value; 'discount' stays, unused.)"""
    op.drop_constraint(op.f('ck_subscription_lines_amount_sign'), 'subscription_lines', type_='check')
    op.create_check_constraint(
        op.f('ck_subscription_lines_not_negative'), 'subscription_lines', 'amount_kobo >= 0 AND quantity >= 0'
    )
