"""Personal email notification settings, and notifications held for a daily summary

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-10 10:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('notify_prefs', postgresql.JSONB(), nullable=False, server_default='{}'))
    op.drop_constraint('mail_outbox_status', 'mail_outbox', type_='check')
    op.create_check_constraint('mail_outbox_status', 'mail_outbox',
                               "status in ('queued','sent','failed','canceled','held','combined')")


def downgrade() -> None:
    op.execute("update mail_outbox set status = 'canceled' where status in ('held', 'combined')")
    op.drop_constraint('mail_outbox_status', 'mail_outbox', type_='check')
    op.create_check_constraint('mail_outbox_status', 'mail_outbox', "status in ('queued','sent','failed','canceled')")
    op.drop_column('users', 'notify_prefs')
