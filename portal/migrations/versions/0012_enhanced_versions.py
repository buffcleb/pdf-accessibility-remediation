"""A version kind for an improved copy of a processing service's output ("Adobe + AI alt text")

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 12:00:00
"""
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('version_kind', 'document_versions', type_='check')
    op.create_check_constraint('version_kind', 'document_versions',
                               "kind in ('original','primary','visual_fallback','enhanced','reviewed')")
    op.execute("""
        update services set notice_text = 'Documents are sent to Adobe PDF Services for tagging and deleted from '
                                          'Adobe after processing. Adobe doesn''t write alternate text for images; '
                                          'this service can add it with AI, in a separate copy.'
        where key = 'adobe' and notice_text = 'Documents are sent to Adobe PDF Services for tagging and deleted from '
                                               'Adobe after processing. Adobe doesn''t write alternate text for images.'
    """)


def downgrade() -> None:
    op.execute("delete from document_versions where kind = 'enhanced'")
    op.drop_constraint('version_kind', 'document_versions', type_='check')
    op.create_check_constraint('version_kind', 'document_versions',
                               "kind in ('original','primary','visual_fallback','reviewed')")
