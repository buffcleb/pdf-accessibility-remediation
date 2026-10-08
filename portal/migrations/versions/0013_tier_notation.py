"""Per AI tier: whether the AI reads specialist notation the layout rules can't (on by default)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 16:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('allow_notation', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column('services', 'allow_notation')
