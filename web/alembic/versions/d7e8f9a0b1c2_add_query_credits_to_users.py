"""add query_credits to users

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b0
Create Date: 2026-05-24

Adds query_credits column to the users table. Credits are purchased in one-time
Stripe blocks (80 / 160 / 320) and consumed by scheduled or on-demand query
checks. Each successful check costs 1 credit. New accounts and existing accounts
default to 0.
"""

import sqlalchemy as sa
from alembic import op

revision = 'd7e8f9a0b1c2'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("query_credits", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("query_credits")
