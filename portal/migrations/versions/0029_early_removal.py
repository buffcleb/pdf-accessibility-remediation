"""Early removal: documents marked done, and documents deleted by super admins

People mark a document done and its files go after a short hold; super admins remove files now, or delete a
document (it leaves the document lists but stays in reports).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-09 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('removal_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('removal_requested_by', sa.String(320), nullable=True))
    op.add_column('jobs', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('deleted_by', sa.String(320), nullable=True))
    # The sweep looks for documents whose hold has ended
    op.create_index('jobs_removal_requested_idx', 'jobs', ['removal_requested_at'],
                    postgresql_where=sa.text('removal_requested_at IS NOT NULL AND files_deleted_at IS NULL'))


def downgrade() -> None:
    op.drop_index('jobs_removal_requested_idx', table_name='jobs')
    op.drop_column('jobs', 'deleted_by')
    op.drop_column('jobs', 'deleted_at')
    op.drop_column('jobs', 'removal_requested_by')
    op.drop_column('jobs', 'removal_requested_at')
