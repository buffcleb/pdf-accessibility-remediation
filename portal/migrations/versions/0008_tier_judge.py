"""A review (judge) model per AI tier

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 18:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('gateway_judge_model', sa.String(200), nullable=True))
    op.execute("update services set gateway_judge_model = 'local-judge' where key = 'ai:local' "
               "and gateway_judge_model is null")


def downgrade() -> None:
    op.drop_column('services', 'gateway_judge_model')
