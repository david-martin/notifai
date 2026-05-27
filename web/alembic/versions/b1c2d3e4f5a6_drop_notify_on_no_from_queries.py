"""drop notify_on_no from queries

Revision ID: b1c2d3e4f5a6
Revises: f0a1b2c3d4e5
Create Date: 2026-05-26

notify_on_no was never read by web/runner.py or any route handler.
It is only meaningful in the self-hosted check.py context (YAML field).
Removing the column from the web DB; the queries.yaml field is unaffected.
Uses batch_alter_table — required for SQLite column drops.
"""

import sqlalchemy as sa
from alembic import op

revision = 'b1c2d3e4f5a6'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('queries') as batch_op:
        batch_op.drop_column('notify_on_no')


def downgrade() -> None:
    with op.batch_alter_table('queries') as batch_op:
        batch_op.add_column(
            sa.Column('notify_on_no', sa.Boolean(), nullable=False, server_default='0')
        )
