"""Subgroups' pages (#431): a subgroup draws on its parent's pool, up to a cap of its own

group_services.cap_pages is the most a subgroup may have used and reserved of a service (None: whatever its parent
has left). Its own row counts what it has used; the pool is its parent's. quota_ledger.subgroup_id names the
subgroup whose person an entry is for, the entry's group being the pool's (the parent). Its row-level security
policy lets the subgroup's people and admins see and write those entries: a subgroup in their groups, under the
entry's group. A new kind of entry, cap, records a change to a subgroup's limit.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-26 01:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0052'
down_revision = '0051'
branch_labels = None
depends_on = None

_GROUP = "portal_rls_bypass() or group_id = any(portal_rls_groups())"
_SUBGROUP = ("(subgroup_id = any(portal_rls_groups()) and exists ("
             "select 1 from groups g where g.id = subgroup_id and g.parent_id = group_id))")
_BEFORE = (f"{_GROUP} or user_id = portal_rls_user()",
           f"{_GROUP} or (group_id is null and user_id = portal_rls_user())")
_AFTER = (f"{_GROUP} or {_SUBGROUP} or user_id = portal_rls_user()",
          f"{_GROUP} or {_SUBGROUP} or (group_id is null and user_id = portal_rls_user())")


def _policy(using: str, check: str) -> None:
    op.execute("drop policy if exists quota_ledger_scoped on quota_ledger")
    op.execute(f"create policy quota_ledger_scoped on quota_ledger using ({using}) with check ({check})")  # nosec B608


_KINDS = "'topup','adjust','reserve','commit','release','correct'"


def _kinds(kinds: str) -> None:
    op.drop_constraint('ledger_kind', 'quota_ledger', type_='check')
    op.create_check_constraint('ledger_kind', 'quota_ledger', f"kind in ({kinds})")


def upgrade() -> None:
    op.add_column('group_services', sa.Column('cap_pages', sa.BigInteger(), nullable=True))
    op.create_check_constraint('group_service_cap', 'group_services', 'cap_pages is null or cap_pages >= 0')
    op.add_column('quota_ledger', sa.Column('subgroup_id', postgresql.UUID(as_uuid=True),
                                            sa.ForeignKey('groups.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_quota_ledger_subgroup_id', 'quota_ledger', ['subgroup_id'])
    _policy(*_AFTER)
    _kinds(_KINDS + ",'cap'")


def downgrade() -> None:
    op.execute("delete from quota_ledger where kind = 'cap'")
    _kinds(_KINDS)
    _policy(*_BEFORE)
    op.drop_index('ix_quota_ledger_subgroup_id', table_name='quota_ledger')
    op.drop_column('quota_ledger', 'subgroup_id')
    op.drop_constraint('group_service_cap', 'group_services', type_='check')
    op.drop_column('group_services', 'cap_pages')
