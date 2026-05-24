"""add completed to queries

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-05-24

Adds a completed column to the queries table. completed=True means the runner
answered YES and auto-deactivated the query. Distinct from active=False (manually
paused). Existing rows default to False.
"""

import sqlalchemy as sa
from alembic import op


def upgrade():
    with op.batch_alter_table("queries") as batch_op:
        batch_op.add_column(
            sa.Column("completed", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade():
    with op.batch_alter_table("queries") as batch_op:
        batch_op.drop_column("completed")
