"""AI tag review of document versions (at save time or later)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 13:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('document_versions', sa.Column('review', sa.String(length=16), nullable=True))
    op.add_column('document_versions', sa.Column('review_status', sa.String(length=16), nullable=True))
    op.add_column('document_versions', sa.Column('review_engine_job_id', sa.String(length=64), nullable=True))
    op.add_column('document_versions', sa.Column('review_requested_by', sa.String(length=320), nullable=True))
    op.add_column('document_versions', sa.Column('review_submitted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('document_versions', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('document_versions', sa.Column('review_error', sa.Text(), nullable=True))
    op.create_index(op.f('ix_document_versions_review_status'), 'document_versions', ['review_status'])
    op.create_check_constraint('version_review', 'document_versions', "review is null or review in ('sampled')")
    op.create_check_constraint('version_review_status', 'document_versions',
                               "review_status is null or review_status in ('queued','running','done','failed')")


def downgrade() -> None:
    op.drop_constraint('version_review_status', 'document_versions', type_='check')
    op.drop_constraint('version_review', 'document_versions', type_='check')
    op.drop_index(op.f('ix_document_versions_review_status'), table_name='document_versions')
    for c in ('review_error', 'reviewed_at', 'review_submitted_at', 'review_requested_by', 'review_engine_job_id',
              'review_status', 'review'):
        op.drop_column('document_versions', c)
