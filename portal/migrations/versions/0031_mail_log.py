"""Email log: every email's backend and message ID, sign-in codes and test emails recorded too

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('mail_outbox', sa.Column('backend', sa.String(16), nullable=True))
    op.add_column('mail_outbox', sa.Column('message_id', sa.String(300), nullable=True))
    op.create_index('mail_outbox_to_idx', 'mail_outbox', ['to_addr'])


def downgrade() -> None:
    op.drop_index('mail_outbox_to_idx', table_name='mail_outbox')
    op.drop_column('mail_outbox', 'message_id')
    op.drop_column('mail_outbox', 'backend')
