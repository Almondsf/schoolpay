"""family split rule

How a school shares one amount a parent pays for several children (the family payment link):
a per-school setting, chosen by the proprietor. Every school starts on "clear_most".

Revision ID: 51b12d29788d
Revises: 4d5c5b8e17cd
Create Date: 2026-10-23 19:12:10.135566

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '51b12d29788d'
down_revision: Union[str, Sequence[str], None] = '4d5c5b8e17cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RULE = postgresql.ENUM('clear_most', 'proportional', 'eldest_first', 'youngest_first', name='family_split_rule')


def upgrade() -> None:
    """Upgrade schema."""
    RULE.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'schools',
        sa.Column(
            'family_split_rule',
            postgresql.ENUM(name='family_split_rule', create_type=False),
            server_default='clear_most',
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('schools', 'family_split_rule')
    RULE.drop(op.get_bind(), checkfirst=True)
