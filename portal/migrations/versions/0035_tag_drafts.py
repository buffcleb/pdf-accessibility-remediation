"""Tag viewer: unsaved edits kept on the server between sessions

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-11 14:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tag_drafts',
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_email', sa.String(320), nullable=False),
        sa.Column('based_on', sa.Integer(), nullable=False),
        sa.Column('edits', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('job_id', 'user_id'),
    )
    op.create_index('tag_drafts_user_idx', 'tag_drafts', ['user_id'])


def downgrade() -> None:
    op.drop_index('tag_drafts_user_idx', table_name='tag_drafts')
    op.drop_table('tag_drafts')
