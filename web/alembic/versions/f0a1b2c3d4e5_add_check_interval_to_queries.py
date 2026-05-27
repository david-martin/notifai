"""add check_interval and next_check_at to queries

Revision ID: f0a1b2c3d4e5
Revises: e1f2a3b4c5d6
Create Date: 2026-05-26

check_interval: string enum ('1d'|'1w'|'1mo'), default '1d'.
next_check_at: nullable datetime; NULL means due immediately.
Existing rows get check_interval='1d', next_check_at=NULL —
unchanged behavior, they all run on the next daily runner fire.
"""

import sqlalchemy as sa
from alembic import op

revision = 'f0a1b2c3d4e5'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'queries',
        sa.Column('check_interval', sa.String(), nullable=False, server_default='1d'),
    )
    op.add_column(
        'queries',
        sa.Column('next_check_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('queries', 'next_check_at')
    op.drop_column('queries', 'check_interval')
