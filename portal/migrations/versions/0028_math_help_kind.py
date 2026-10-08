"""US English: the help-request kind "maths" is now "math"

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-09 09:00:00
"""
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None

OLD = "('alt_text', 'reading_order', 'tables', 'headings', 'maths', 'visual', 'other')"
NEW = "('alt_text', 'reading_order', 'tables', 'headings', 'math', 'visual', 'other')"


def upgrade() -> None:
    op.drop_constraint('help_kind', 'help_requests', type_='check')
    op.execute("UPDATE help_requests SET kind = 'math' WHERE kind = 'maths'")
    op.create_check_constraint('help_kind', 'help_requests', f"kind in {NEW}")


def downgrade() -> None:
    op.drop_constraint('help_kind', 'help_requests', type_='check')
    op.execute("UPDATE help_requests SET kind = 'maths' WHERE kind = 'math'")
    op.create_check_constraint('help_kind', 'help_requests', f"kind in {OLD}")
