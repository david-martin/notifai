"""hash session tokens

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-23

Invalidates all existing sessions so stored token hashes are consistent.
"""

from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column("id", type_=sa.String(64))
    op.execute("DELETE FROM sessions")


def downgrade():
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column("id", type_=sa.String(36))
    op.execute("DELETE FROM sessions")
