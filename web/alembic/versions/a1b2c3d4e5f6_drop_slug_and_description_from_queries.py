"""drop slug and description from queries

Revision ID: a1b2c3d4e5f6
Revises: 4287893806cb
Create Date: 2026-05-23 00:00:00.000000

"""
from alembic import op


revision = 'a1b2c3d4e5f6'
down_revision = '4287893806cb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('queries', 'slug')
    op.drop_column('queries', 'description')


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column('queries', sa.Column('description', sa.String(), nullable=False, server_default=''))
    op.add_column('queries', sa.Column('slug', sa.String(), nullable=False, server_default=''))
