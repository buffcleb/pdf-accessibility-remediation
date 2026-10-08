"""Help requests: people ask for help with a remediated document and talk it over with super admins

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

KINDS = "('alt_text', 'reading_order', 'tables', 'headings', 'maths', 'visual', 'other')"
STATUSES = "('open', 'in_progress', 'waiting', 'resolved', 'closed')"


def upgrade() -> None:
    ts = lambda name, **kw: sa.Column(name, sa.DateTime(timezone=True), **kw)  # noqa: E731
    op.create_table(
        'help_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('groups.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=True),
        sa.Column('version_label', sa.String(100), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('tag', postgresql.JSONB(), nullable=True),
        sa.Column('assignee_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('assignee_email', sa.String(320), nullable=True),
        sa.Column('saved', sa.Boolean(), nullable=False, server_default=sa.false()),
        ts('created_at', nullable=False, server_default=sa.func.now()),
        ts('updated_at', nullable=False, server_default=sa.func.now()),
        ts('person_wrote_at', nullable=True),
        ts('staff_wrote_at', nullable=True),
        ts('person_seen_at', nullable=True),
        ts('status_changed_at', nullable=True),
        ts('person_notified_at', nullable=True),
        sa.Column('person_notify_pending', sa.Boolean(), nullable=False, server_default=sa.false()),
        ts('staff_notified_at', nullable=True),
        sa.Column('staff_notify_pending', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(f"kind in {KINDS}", name='help_kind'),
        sa.CheckConstraint(f"status in {STATUSES}", name='help_status'),
    )
    op.create_index('ix_help_requests_job_id', 'help_requests', ['job_id'])
    op.create_index('ix_help_requests_user_id', 'help_requests', ['user_id'])
    op.create_index('ix_help_requests_group_id', 'help_requests', ['group_id'])
    op.create_index('ix_help_requests_status', 'help_requests', ['status'])
    op.create_index('help_one_active_per_job', 'help_requests', ['job_id'], unique=True,
                    postgresql_where=sa.text("status in ('open','in_progress','waiting')"))

    op.create_table(
        'help_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('help_requests.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('author_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('author_email', sa.String(320), nullable=False),
        sa.Column('author_role', sa.String(16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        ts('created_at', nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('message','note','event')", name='help_message_kind'),
        sa.CheckConstraint("author_role in ('person','group_admin','super_admin','system')", name='help_author_role'),
    )
    op.create_index('ix_help_messages_request_id', 'help_messages', ['request_id'])

    op.create_table(
        'help_attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('help_messages.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('help_requests.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('s3_key', sa.String(512), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        ts('created_at', nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_help_attachments_message_id', 'help_attachments', ['message_id'])
    op.create_index('ix_help_attachments_request_id', 'help_attachments', ['request_id'])


def downgrade() -> None:
    op.drop_table('help_attachments')
    op.drop_table('help_messages')
    op.drop_table('help_requests')
