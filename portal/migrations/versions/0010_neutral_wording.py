"""Wording for any organization, not just campuses: update the seeded texts nobody has edited

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06 23:00:00
"""
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

CHANGES = [
    ("standard", "Remediation by this service''s own engine, on campus systems.",
     "Remediation by this service''s own engine, on this service''s own servers."),
    ("ai:local", "AI models running on campus servers.", "AI models running on this service''s own servers."),
]


# The SQL is built from CHANGES above (constants, quotes already doubled), never from input
def upgrade() -> None:
    for key, old, new in CHANGES:
        op.execute(f"update services set description = '{new}' where key = '{key}' and description = '{old}'")  # nosec B608


def downgrade() -> None:
    for key, old, new in CHANGES:
        op.execute(f"update services set description = '{old}' where key = '{key}' and description = '{new}'")  # nosec B608
