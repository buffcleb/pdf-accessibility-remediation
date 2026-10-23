"""Acknowledged Needs attention items (#538)

- attention_acks: an item someone acknowledged, for everyone who sees that list (super admins', scope "all", or one
  group's admins', scope the group's id), with the things it was about then (documents, emails, an alert), who and
  when. The item comes back when something new joins them. Group-scoped like the group's other rows (row-level
  security); super admins' rows (no group) are theirs only.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-22 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None

_GROUP = "portal_rls_bypass() or group_id = any(portal_rls_groups())"


def upgrade() -> None:
    op.create_table(
        "attention_acks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=True,
                  index=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("acked_by", sa.String(320), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "key", name="uq_attention_ack"),
    )
    op.execute("alter table attention_acks enable row level security")
    op.execute(f"create policy attention_acks_scoped on attention_acks using ({_GROUP}) with check ({_GROUP})")


def downgrade() -> None:
    op.drop_table("attention_acks")
