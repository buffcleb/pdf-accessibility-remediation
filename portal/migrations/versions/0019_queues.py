"""Queues: pausing them, and an outbox for email so it can be seen, paused and retried

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-08 20:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'queue_pauses',
        sa.Column('key', sa.String(80), primary_key=True),
        sa.Column('paused_by', sa.String(320), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('paused_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'mail_outbox',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('to_addr', sa.String(320), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('from_name', sa.String(200), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_try_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('queued','sent','failed','canceled')", name='mail_outbox_status'),
    )
    op.create_index('mail_outbox_status_next_idx', 'mail_outbox', ['status', 'next_try_at'])
    op.create_index('mail_outbox_created_idx', 'mail_outbox', ['created_at'])


def downgrade() -> None:
    op.drop_index('mail_outbox_created_idx', table_name='mail_outbox')
    op.drop_index('mail_outbox_status_next_idx', table_name='mail_outbox')
    op.drop_table('mail_outbox')
    op.drop_table('queue_pauses')
