"""drop creation_attempts columns from users

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-05-25

creation_attempts_this_month and creation_attempts_reset_at were added
to track per-month query creation rate limiting, but were never wired up
to any router or enforced anywhere. The active query cap (max 20 active
queries per user) is now enforced at create time instead. These dead
columns are removed.
"""

import sqlalchemy as sa
from alembic import op

revision = 'e1f2a3b4c5d6'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("creation_attempts_this_month")
        batch_op.drop_column("creation_attempts_reset_at")


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("creation_attempts_this_month", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("creation_attempts_reset_at", sa.Date(), nullable=True)
        )
