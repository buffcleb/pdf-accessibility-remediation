"""The "Local AI" tier: the local models behind the AI gateway (switched off until a super admin turns it on)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 14:00:00
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        insert into services (key, kind, label, description, enabled, external, notice_enabled, metered, used_pages,
                              reserved_pages, confirm_before_send, gateway_model, sort)
        values ('ai:local', 'ai_tier', 'Local AI', 'AI models running on this service''s own servers.', false, false, false,
                false, 0, 0, false, 'local-vision', 100)
        on conflict (key) do nothing
    """)


def downgrade() -> None:
    op.execute("delete from services where key = 'ai:local' and used_pages = 0 and reserved_pages = 0")
