"""Upload choices: how a group gets its AI, the external-provider badge, and AI in audits

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-08 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('ai_mode', sa.String(8), nullable=False, server_default='choose'))
    op.add_column('groups', sa.Column('ai_level', sa.String(64), nullable=True))
    op.add_column('groups', sa.Column('ai_kind', sa.String(40), nullable=True))
    op.add_column('groups', sa.Column('show_external', sa.Boolean(), nullable=False, server_default='false'))
    op.create_check_constraint('group_ai_mode', 'groups', "ai_mode in ('choose','fixed')")
    op.add_column('services', sa.Column('audit_ai', sa.String(8), nullable=False, server_default='free'))
    op.create_check_constraint('service_audit_ai', 'services', "audit_ai in ('free','charge','off')")
    op.add_column('jobs', sa.Column('no_ai', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('jobs', 'no_ai')
    op.drop_constraint('service_audit_ai', 'services', type_='check')
    op.drop_column('services', 'audit_ai')
    op.drop_constraint('group_ai_mode', 'groups', type_='check')
    op.drop_column('groups', 'show_external')
    op.drop_column('groups', 'ai_kind')
    op.drop_column('groups', 'ai_level')
    op.drop_column('groups', 'ai_mode')
