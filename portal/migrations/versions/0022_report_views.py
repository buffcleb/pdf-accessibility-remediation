"""Scheduled report views: saved views emailed on a schedule, and CSV attachments in the outbox

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-09 16:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('mail_outbox', sa.Column('attachments', postgresql.JSONB(), nullable=True))
    op.create_table(
        'saved_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('report', sa.String(40), nullable=False),
        sa.Column('params', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('groups.id', ondelete='CASCADE'),
                  nullable=True),
        sa.Column('frequency', sa.String(16), nullable=False, server_default='monthly'),
        sa.Column('recipients', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('attach_csv', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("frequency in ('monthly','weekly','daily')", name='saved_report_frequency'),
    )
    op.create_index('ix_saved_reports_owner_id', 'saved_reports', ['owner_id'])
    op.add_column('report_sends', sa.Column('saved_report_id', postgresql.UUID(as_uuid=True),
                                            sa.ForeignKey('saved_reports.id', ondelete='SET NULL'), nullable=True))
    # A person gets each built-in period once, and each scheduled view once per period
    op.drop_constraint('report_send_once', 'report_sends', type_='unique')
    op.create_index('report_send_once', 'report_sends', ['user_id', 'frequency', 'period_start'], unique=True,
                    postgresql_where=sa.text('saved_report_id is null'))
    op.create_index('report_view_send_once', 'report_sends', ['saved_report_id', 'user_id', 'period_start'],
                    unique=True, postgresql_where=sa.text('saved_report_id is not null'))


def downgrade() -> None:
    op.drop_index('report_view_send_once', table_name='report_sends')
    op.drop_index('report_send_once', table_name='report_sends')
    op.create_unique_constraint('report_send_once', 'report_sends', ['user_id', 'frequency', 'period_start'])
    op.drop_column('report_sends', 'saved_report_id')
    op.drop_index('ix_saved_reports_owner_id', table_name='saved_reports')
    op.drop_table('saved_reports')
    op.drop_column('mail_outbox', 'attachments')
