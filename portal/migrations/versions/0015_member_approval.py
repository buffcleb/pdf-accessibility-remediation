"""Member approval: per-group "new members need approval", and each account's approval state

Existing accounts are approved and existing groups don't require approval, so nothing changes for them.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-08 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('require_approval', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('approval', sa.String(16), nullable=False, server_default='approved'))
    op.add_column('users', sa.Column('approval_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('approval_by', sa.String(320), nullable=True))
    op.add_column('users', sa.Column('decline_reason', sa.Text(), nullable=True))
    op.create_check_constraint('user_approval', 'users', "approval in ('pending','approved','declined')")
    op.create_index('ix_users_group_pending', 'users', ['group_id'], postgresql_where=sa.text("approval = 'pending'"))


def downgrade() -> None:
    op.drop_index('ix_users_group_pending', table_name='users')
    op.drop_constraint('user_approval', 'users', type_='check')
    op.drop_column('users', 'decline_reason')
    op.drop_column('users', 'approval_by')
    op.drop_column('users', 'approval_at')
    op.drop_column('users', 'approval')
    op.drop_column('groups', 'require_approval')
