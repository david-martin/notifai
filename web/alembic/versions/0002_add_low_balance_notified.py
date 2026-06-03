"""add low_balance_notified to users

Revision ID: 0002_add_low_balance_notified
Revises: 0001_initial_schema
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_add_low_balance_notified'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'low_balance_notified',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'low_balance_notified')
