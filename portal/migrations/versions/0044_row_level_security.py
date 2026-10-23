"""Row-level security on the group-scoped tables (#350)

A second check on group boundaries, behind the API's own. The API sets, for each transaction (portal/rls.py):
portal.group_ids (the signed-in person's group and the groups they administer), portal.user_id (the person),
portal.email (only while someone signs in) and portal.bypass = on (super admins, the worker and other cross-group
work). A row a policy doesn't admit is simply not there for the query.

The policies bind the portal's own account (portal_app, #337), which owns no table. The owner (portal) runs the
migrations and the backup's dump, and as owner it isn't bound (FORCE is not set): a dump run as it would otherwise
fail or leave rows out. The audit log takes new rows from anyone; reading, changing or deleting them is scoped.

KEDA counts the waiting work as portal_app: portal_waiting_work() counts it as the owner, and gives only the count.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-20 02:00:00
"""
from alembic import op

revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None

_BYPASS_OR_GROUP = "portal_rls_bypass() or group_id = any(portal_rls_groups())"
# The rows a person may see: their group's, the groups they administer, their own. What they write must stay in
# those groups: their own row with no group (a global ledger entry) is the only other kind.
_GROUP_OR_OWN = (f"{_BYPASS_OR_GROUP} or user_id = portal_rls_user()",
                 f"{_BYPASS_OR_GROUP} or (group_id is null and user_id = portal_rls_user())")
# Rows that belong to a document or a help request are there when it is (its own policy applies in the subquery)
_OF_JOB = "portal_rls_bypass() or exists (select 1 from jobs j where j.id = job_id)"
_OF_REQUEST = "portal_rls_bypass() or exists (select 1 from help_requests r where r.id = request_id)"

# table: the rows a query sees (using), or (using, what a write may leave: with check)
POLICIES = {
    "jobs": _GROUP_OR_OWN,
    "job_files": _OF_JOB,
    "document_versions": _OF_JOB,
    "tag_drafts": _OF_JOB,
    "finding_marks": _OF_JOB,
    "quota_ledger": _GROUP_OR_OWN,
    "help_requests": _GROUP_OR_OWN,
    "help_messages": _OF_REQUEST,
    "help_attachments": _OF_REQUEST,
    "saved_reports": (f"{_BYPASS_OR_GROUP} or owner_id = portal_rls_user()",
                      f"{_BYPASS_OR_GROUP} or (group_id is null and owner_id = portal_rls_user())"),
    # A person's own row, by id once signed in or by address while signing in; their group's people; and, for
    # someone signed in, the super admins (who answer help requests and are told about new members). Only the
    # address being signed in with may be written outside the person's groups: a new account, placed by the API.
    "users": (f"{_BYPASS_OR_GROUP} or id = portal_rls_user() or email = portal_rls_email()"  # nosec B608 - constants
              " or (portal_rls_user() is not null and exists ("
              "select 1 from role_assignments r where r.user_id = users.id and r.role = 'super_admin'))",
              f"{_BYPASS_OR_GROUP} or email = portal_rls_email()"),
}
AUDIT = _BYPASS_OR_GROUP


def upgrade() -> None:
    op.execute("""
        create or replace function portal_rls_bypass() returns boolean language sql stable as $$
            select coalesce(current_setting('portal.bypass', true), '') = 'on' $$;
        create or replace function portal_rls_groups() returns uuid[] language sql stable as $$
            select coalesce(string_to_array(nullif(current_setting('portal.group_ids', true), ''), ',')::uuid[],
                            '{}'::uuid[]) $$;
        create or replace function portal_rls_user() returns uuid language sql stable as $$
            select nullif(current_setting('portal.user_id', true), '')::uuid $$;
        create or replace function portal_rls_email() returns text language sql stable as $$
            select nullif(current_setting('portal.email', true), '') $$;
        create or replace function portal_waiting_work() returns bigint language sql stable security definer
            set search_path = public, pg_temp as $$
            select (select count(*) from jobs where status in ('submitted','processing')
                    or (status = 'queued' and coalesce(progress->>'stage','') not in
                        ('paused','waiting_daily_cap','waiting_at_once')))
                 + (select count(*) from document_versions where status in ('queued','submitted','processing')) $$;
    """)
    for table, rule in POLICIES.items():
        using, check = rule if isinstance(rule, tuple) else (rule, rule)
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"create policy {table}_scoped on {table} using ({using}) with check ({check})")
    op.execute("alter table audit_events enable row level security")
    op.execute("create policy audit_events_add on audit_events for insert with check (true)")
    op.execute(f"create policy audit_events_read on audit_events for select using ({AUDIT})")
    op.execute(f"create policy audit_events_change on audit_events for update using ({AUDIT}) with check ({AUDIT})")
    op.execute(f"create policy audit_events_purge on audit_events for delete using ({AUDIT})")


def downgrade() -> None:
    for name in ("add", "read", "change", "purge"):
        op.execute(f"drop policy if exists audit_events_{name} on audit_events")
    op.execute("alter table audit_events disable row level security")
    for table in POLICIES:
        op.execute(f"drop policy if exists {table}_scoped on {table}")
        op.execute(f"alter table {table} disable row level security")
    op.execute("""
        drop function if exists portal_waiting_work();
        drop function if exists portal_rls_email();
        drop function if exists portal_rls_user();
        drop function if exists portal_rls_groups();
        drop function if exists portal_rls_bypass();
    """)
