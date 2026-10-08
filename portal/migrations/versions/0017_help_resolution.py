"""Help requests, part B: how a request ended (what was done, a corrected version, pages given back),
reminders while waiting on the person, and versions made from a corrected file uploaded by support

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08 15:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('help_requests', sa.Column('resolution', sa.Text(), nullable=True))
    op.add_column('help_requests', sa.Column('resolved_version_number', sa.Integer(), nullable=True))
    op.add_column('help_requests', sa.Column('pages_refunded', sa.Integer(), nullable=True))
    op.add_column('help_requests', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('help_requests', sa.Column('reminders_sent', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('document_versions', sa.Column('source_file_id', postgresql.UUID(as_uuid=True),
                                                 sa.ForeignKey('job_files.id', ondelete='SET NULL'), nullable=True))
    op.drop_constraint('version_kind', 'document_versions', type_='check')
    op.create_check_constraint('version_kind', 'document_versions',
                               "kind in ('original','primary','visual_fallback','enhanced','reviewed','corrected')")


def downgrade() -> None:
    op.drop_constraint('version_kind', 'document_versions', type_='check')
    op.execute("delete from document_versions where kind = 'corrected'")
    op.create_check_constraint('version_kind', 'document_versions',
                               "kind in ('original','primary','visual_fallback','enhanced','reviewed')")
    op.drop_column('document_versions', 'source_file_id')
    op.drop_column('help_requests', 'reminders_sent')
    op.drop_column('help_requests', 'finished_at')
    op.drop_column('help_requests', 'pages_refunded')
    op.drop_column('help_requests', 'resolved_version_number')
    op.drop_column('help_requests', 'resolution')
