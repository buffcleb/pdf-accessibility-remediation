"""A saved version can be opened while its checks are still running (#428)

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-19 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('document_versions', sa.Column('checking', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('document_versions', 'checking')
