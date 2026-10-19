"""Documents: a super admin's re-run is a new document that says which one it re-ran (#323)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-16 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('rerun_of', postgresql.UUID(as_uuid=True),
                                    sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_jobs_rerun_of', 'jobs', ['rerun_of'])


def downgrade() -> None:
    op.drop_index('ix_jobs_rerun_of', table_name='jobs')
    op.drop_column('jobs', 'rerun_of')
