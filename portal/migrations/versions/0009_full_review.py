"""'Review every figure': per upload, per saved-version review, and allowed per AI tier

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 21:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('allow_full_review', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('jobs', sa.Column('full_review', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint('version_review', 'document_versions', type_='check')
    op.create_check_constraint('version_review', 'document_versions', "review is null or review in ('sampled','full')")


def downgrade() -> None:
    op.drop_constraint('version_review', 'document_versions', type_='check')
    op.create_check_constraint('version_review', 'document_versions', "review is null or review in ('sampled')")
    op.drop_column('jobs', 'full_review')
    op.drop_column('services', 'allow_full_review')
