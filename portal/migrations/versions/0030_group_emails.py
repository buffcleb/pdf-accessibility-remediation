"""People allowed into a group by their address, without their whole domain

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-09 14:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'group_emails',
        sa.Column('email', sa.String(320), primary_key=True),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('groups.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('note', sa.String(300), nullable=True),
        sa.Column('added_by', sa.String(320), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('email = lower(email)', name='group_email_lower'),
    )
    op.create_index(op.f('ix_group_emails_group_id'), 'group_emails', ['group_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_group_emails_group_id'), table_name='group_emails')
    op.drop_table('group_emails')
