"""Tag viewer: "Describe with AI", allowed per group and per person (None follows the level above)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-14 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('ai_describe', sa.Boolean(), nullable=True))
    op.add_column('users', sa.Column('ai_describe', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'ai_describe')
    op.drop_column('groups', 'ai_describe')
