"""PDF/UA-2 for PDF 1.x documents (#445): a group's choice and default, and each document's part

groups.pdfua_choice lets the group's people choose PDF/UA-2 or Both (once the service offers it: the pdfua2_enabled
setting); groups.pdfua_default is what's preselected; jobs.pdfua is what a document was run for ("1", "2", "both").
The downgrade drops them: documents run for PDF/UA-2 keep their files, and reruns go back to PDF/UA-1.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-24 21:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0050'
down_revision = '0049'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('pdfua_choice', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('groups', sa.Column('pdfua_default', sa.String(4), nullable=False, server_default='1'))
    op.add_column('jobs', sa.Column('pdfua', sa.String(4), nullable=False, server_default='1'))
    op.create_check_constraint('group_pdfua_default', 'groups', "pdfua_default in ('1','2','both')")
    op.create_check_constraint('job_pdfua', 'jobs', "pdfua in ('1','2','both')")


def downgrade() -> None:
    op.drop_constraint('job_pdfua', 'jobs', type_='check')
    op.drop_constraint('group_pdfua_default', 'groups', type_='check')
    op.drop_column('jobs', 'pdfua')
    op.drop_column('groups', 'pdfua_default')
    op.drop_column('groups', 'pdfua_choice')
