"""Reviewed versions: saving into the same one (the one before is kept a day for undo)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-11 16:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('document_versions', sa.Column('replaces_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('document_versions_replaces_fk', 'document_versions', 'document_versions',
                          ['replaces_id'], ['id'], ondelete='SET NULL')
    op.add_column('document_versions', sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_document_versions_superseded_at', 'document_versions', ['superseded_at'])


def downgrade() -> None:
    op.drop_index('ix_document_versions_superseded_at', table_name='document_versions')
    op.drop_column('document_versions', 'superseded_at')
    op.drop_constraint('document_versions_replaces_fk', 'document_versions', type_='foreignkey')
    op.drop_column('document_versions', 'replaces_id')
