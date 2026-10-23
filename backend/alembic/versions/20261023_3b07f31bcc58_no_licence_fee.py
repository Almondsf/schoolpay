"""no licence fee

WardPay charges a set-up fee and a pupil fee each term, and no licence (owner, 2026-10-23). Bills
already issued keep any licence lines they have.

Revision ID: 3b07f31bcc58
Revises: 51b12d29788d
Create Date: 2026-10-23 19:50:55.487281

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b07f31bcc58'
down_revision: Union[str, Sequence[str], None] = '51b12d29788d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Dropping the column also drops the prices check that named it, so it's made again without it
    op.drop_column('plans', 'annual_fee_kobo')
    op.create_check_constraint(
        op.f('ck_plans_prices_not_negative'), 'plans', 'setup_fee_kobo >= 0 AND per_pupil_year_kobo >= 0'
    )
    op.execute("""
        UPDATE plans SET description = 'Set-up fee, and a fee per pupil per year billed termly'
        WHERE description = 'Set-up fee, annual licence, and a fee per pupil per year billed termly'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_plans_prices_not_negative'), 'plans', type_='check')
    op.add_column('plans', sa.Column('annual_fee_kobo', sa.BIGINT(), server_default='0', nullable=False))
    op.create_check_constraint(
        op.f('ck_plans_prices_not_negative'),
        'plans',
        'setup_fee_kobo >= 0 AND annual_fee_kobo >= 0 AND per_pupil_year_kobo >= 0',
    )
