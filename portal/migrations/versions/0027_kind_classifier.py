"""Kind-of-document classifier: "Let AI decide" jobs, and what the classifier picked

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-08 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('ai_kind_auto', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('jobs', sa.Column('ai_kind_pick', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'ai_kind_pick')
    op.drop_column('jobs', 'ai_kind_auto')
