"""No AI as a choice for remediation (#659): a group's setting

groups.no_ai offers No AI beside the group's AI levels, once super admins offer it (the no_ai_enabled setting):
"off", "on", or "default" (preselected). A group sets No AI as its only choice with ai_mode "fixed" and ai_level
"none". Subgroups take it from their parent with the other AI settings. The downgrade drops the column; a group fixed
to No AI keeps ai_level "none", which uploads then refuse until an admin picks a level.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-29 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0054'
down_revision = '0053'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('no_ai', sa.String(8), nullable=False, server_default='off'))
    op.create_check_constraint('group_no_ai', 'groups', "no_ai in ('off','on','default')")


def downgrade() -> None:
    op.drop_constraint('group_no_ai', 'groups', type_='check')
    op.drop_column('groups', 'no_ai')
