"""Don't email what someone already saw in the portal: what's open, what each email is about

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-10 16:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_presence',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('item', sa.String(80), primary_key=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column('mail_outbox', sa.Column('about', sa.String(80), nullable=True))
    op.drop_constraint('mail_outbox_status', 'mail_outbox', type_='check')
    op.create_check_constraint('mail_outbox_status', 'mail_outbox',
                               "status in ('queued','sent','failed','canceled','held','combined','seen')")


def downgrade() -> None:
    op.execute("update mail_outbox set status = 'canceled' where status = 'seen'")
    op.drop_constraint('mail_outbox_status', 'mail_outbox', type_='check')
    op.create_check_constraint('mail_outbox_status', 'mail_outbox',
                               "status in ('queued','sent','failed','canceled','held','combined')")
    op.drop_column('mail_outbox', 'about')
    op.drop_table('user_presence')
