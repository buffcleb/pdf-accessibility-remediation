"""A document can be tagged afresh, its own tags ignored (#396)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-18 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('ignore_existing', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('jobs', 'ignore_existing')
