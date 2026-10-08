"""AI levels: kinds of document within a level, and the kind each document used

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-11 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_kinds',
        sa.Column('service_key', sa.String(64), sa.ForeignKey('services.key', ondelete='CASCADE'), primary_key=True),
        sa.Column('key', sa.String(40), primary_key=True),
        sa.Column('label', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('gateway_model', sa.String(200), nullable=False),
        sa.Column('gateway_judge_model', sa.String(200), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("key <> 'general'", name='ai_kind_not_general'),
    )
    op.add_column('jobs', sa.Column('ai_kind', sa.String(40), nullable=True))
    op.add_column('jobs', sa.Column('ai_kind_fallback', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'ai_kind_fallback')
    op.drop_column('jobs', 'ai_kind')
    op.drop_table('ai_kinds')
