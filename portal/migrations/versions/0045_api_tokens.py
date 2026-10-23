"""Personal API tokens for scripts (#518)

A person makes a token for a script that submits documents (and, if they choose, checks on them and downloads the
results). Each token is tied to one processing service, AI level and kind; only a keyed hash of it is kept. Tokens
are switched on from the top down: for the service (a setting), for a group (here), then made by people.

- api_tokens: the tokens, with their permissions, what they submit to, and when they expire, were last used or
  were revoked. Group-scoped like documents (row-level security).
- api_token_downloads: which finished documents a token has downloaded, so a polling job asks only for new ones.
- groups.api_tokens_enabled: the group's admins let its people make tokens.
- jobs.api_token_id: the token a document was sent with ("Sent by script").

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-21 18:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None

_GROUP_OR_OWN = ("portal_rls_bypass() or group_id = any(portal_rls_groups()) or user_id = portal_rls_user()",
                 "portal_rls_bypass() or group_id = any(portal_rls_groups()) or (group_id is null and user_id = portal_rls_user())")
_OF_TOKEN = "portal_rls_bypass() or exists (select 1 from api_tokens t where t.id = token_id)"


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=True,
                  index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("can_submit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_status", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download", sa.String(16), nullable=True),  # None: no downloads | "all": the zip | "files"
        sa.Column("download_kinds", postgresql.JSONB(), nullable=True),  # with "files": which of them
        sa.Column("processing", sa.String(64), nullable=False),
        sa.Column("ai_tier", sa.String(64), nullable=True),
        sa.Column("ai_kind", sa.String(40), nullable=True),
        sa.Column("auto_confirm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(320), nullable=True),
        sa.Column("revoked_reason", sa.String(32), nullable=True),
    )
    op.create_table(
        "api_token_downloads",
        sa.Column("token_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_tokens.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("groups", sa.Column("api_tokens_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("api_token_id", postgresql.UUID(as_uuid=True),
                                    sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True))
    for table, (using, check) in {"api_tokens": _GROUP_OR_OWN, "api_token_downloads": (_OF_TOKEN, _OF_TOKEN)}.items():
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"create policy {table}_scoped on {table} using ({using}) with check ({check})")


def downgrade() -> None:
    op.drop_column("jobs", "api_token_id")
    op.drop_column("groups", "api_tokens_enabled")
    op.drop_table("api_token_downloads")
    op.drop_table("api_tokens")
