"""API token notices (#518, part 2): when a token's owner was told it expires soon, and that it stopped unused

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-21 21:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("expiry_warned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_tokens", sa.Column("idle_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_tokens", "idle_notified_at")
    op.drop_column("api_tokens", "expiry_warned_at")
