"""Queues: failed items a super admin removed from a queue's list

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-10 21:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'queue_removals',
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('removed_by', sa.String(320), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('kind', 'item_id'),
    )


def downgrade() -> None:
    op.drop_table('queue_removals')
