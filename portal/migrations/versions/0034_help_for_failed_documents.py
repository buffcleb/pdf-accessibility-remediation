"""Help requests: a kind for documents that failed

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-11 09:00:00
"""
from alembic import op

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

OLD = "('alt_text', 'reading_order', 'tables', 'headings', 'math', 'visual', 'other')"
NEW = "('alt_text', 'reading_order', 'tables', 'headings', 'math', 'visual', 'other', 'failed')"


def upgrade() -> None:
    op.drop_constraint('help_kind', 'help_requests', type_='check')
    op.create_check_constraint('help_kind', 'help_requests', f"kind in {NEW}")


def downgrade() -> None:
    op.drop_constraint('help_kind', 'help_requests', type_='check')
    op.execute("UPDATE help_requests SET kind = 'other' WHERE kind = 'failed'")
    op.create_check_constraint('help_kind', 'help_requests', f"kind in {OLD}")
