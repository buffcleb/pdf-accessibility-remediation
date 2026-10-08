"""Scheduled email reports: subscriptions, the record of what was sent, and HTML email

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-09 10:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('mail_outbox', sa.Column('html', sa.Text(), nullable=True))
    op.create_table(
        'report_subscriptions',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('kind', sa.String(32), primary_key=True),
        sa.Column('frequency', sa.String(16), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind in ('my_month','group_summary','service_summary')", name='report_sub_kind'),
        sa.CheckConstraint("frequency in ('monthly','weekly','daily','off')", name='report_sub_frequency'),
    )
    op.create_table(
        'report_sends',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('frequency', sa.String(16), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kinds', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('outbox_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('mail_outbox.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'frequency', 'period_start', name='report_send_once'),
    )
    op.create_index('report_sends_created_idx', 'report_sends', ['created_at'])


def downgrade() -> None:
    op.drop_index('report_sends_created_idx', table_name='report_sends')
    op.drop_table('report_sends')
    op.drop_table('report_subscriptions')
    op.drop_column('mail_outbox', 'html')
