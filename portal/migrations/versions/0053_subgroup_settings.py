"""Subgroups' settings (#431): what a subgroup sets for itself rather than taking from its parent

groups.overrides lists the settings (portal/inherit.py: ai, local_only, require_approval, ai_describe,
show_external, over_quota_message) a subgroup has set itself; the others follow its parent's, which are copied
to it as they change. Existing subgroups start by following their parent: their settings are copied from it now,
local-only staying on where either has it.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-26 10:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0053'
down_revision = '0052'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('overrides', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.execute("""
        update groups s set ai_mode = p.ai_mode, ai_level = p.ai_level, ai_kind = p.ai_kind,
            local_only = s.local_only or p.local_only, require_approval = p.require_approval,
            ai_describe = p.ai_describe, show_external = p.show_external, over_quota_message = p.over_quota_message,
            overrides = case when s.local_only and not p.local_only then '["local_only"]'::jsonb else '[]'::jsonb end
        from groups p where s.parent_id = p.id
    """)


def downgrade() -> None:
    op.drop_column('groups', 'overrides')
