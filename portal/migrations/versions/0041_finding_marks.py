"""Review items signed off per document: checked, fixed or not an issue (#366)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-17 18:30:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'finding_marks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('jobs.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('key', sa.String(40), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('set_by_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('set_by_email', sa.String(320), nullable=False),
        sa.Column('set_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('job_id', 'key'),
        sa.CheckConstraint("status in ('checked', 'fixed', 'not_issue')", name='finding_marks_status'),
    )


def downgrade() -> None:
    op.drop_table('finding_marks')
