"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-28

"""
from alembic import op
import sqlalchemy as sa


revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('notify_email', sa.String(), nullable=True),
        sa.Column('tier', sa.String(), nullable=False),
        sa.Column('query_credits', sa.Integer(), nullable=False),
        sa.Column('stripe_customer_id', sa.String(), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.create_table(
        'magic_links',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_magic_links_token_hash'), 'magic_links', ['token_hash'], unique=True)

    op.create_table(
        'sessions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'queries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('query_text', sa.String(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('check_interval', sa.String(), nullable=False),
        sa.Column('next_check_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'notification_log',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('query_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('checked_at', sa.DateTime(), nullable=False),
        sa.Column('answer', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('sources', sa.String(), nullable=True),
        sa.Column('email_sent', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['query_id'], ['queries.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('notification_log')
    op.drop_table('queries')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_magic_links_token_hash'), table_name='magic_links')
    op.drop_table('magic_links')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
