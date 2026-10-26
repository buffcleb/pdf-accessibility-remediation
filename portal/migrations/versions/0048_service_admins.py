"""Service admins (#564): a role between super admin and group admin, for every group

- role_assignments: role 'service_admin', with no group like 'super_admin', at most one per person.
- help_messages: a service admin may write in a help request.
- users (row-level security): people signed in see the service admins' rows as they see the super admins', since
  both answer help requests. Service admins themselves work across groups with the bypass (portal/rls.py).

The downgrade removes every service admin role first (those people keep their accounts and groups), and their help
messages are kept as written by a super admin.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-23 12:00:00
"""
from alembic import op

revision = '0048'
down_revision = '0047'
branch_labels = None
depends_on = None

_GROUP = "portal_rls_bypass() or group_id = any(portal_rls_groups())"


def _users_policy(roles: str) -> None:
    using = (f"{_GROUP} or id = portal_rls_user() or email = portal_rls_email()"  # nosec B608 - constants
             " or (portal_rls_user() is not null and exists ("
             f"select 1 from role_assignments r where r.user_id = users.id and r.role in ({roles})))")
    op.execute("drop policy users_scoped on users")
    op.execute(f"create policy users_scoped on users using ({using}) "
               f"with check ({_GROUP} or email = portal_rls_email())")


def upgrade() -> None:
    op.drop_constraint("role_scope", "role_assignments", type_="check")
    op.create_check_constraint(
        "role_scope", "role_assignments",
        "(role in ('super_admin','service_admin') and group_id is null) or (role = 'group_admin' and group_id is not null)")
    op.create_index("role_one_service_admin", "role_assignments", ["user_id"], unique=True,
                    postgresql_where="role = 'service_admin'")
    op.drop_constraint("help_author_role", "help_messages", type_="check")
    op.create_check_constraint("help_author_role", "help_messages",
                               "author_role in ('person','group_admin','service_admin','super_admin','system')")
    _users_policy("'super_admin', 'service_admin'")


def downgrade() -> None:
    _users_policy("'super_admin'")
    op.execute("update help_messages set author_role = 'super_admin' where author_role = 'service_admin'")
    op.drop_constraint("help_author_role", "help_messages", type_="check")
    op.create_check_constraint("help_author_role", "help_messages",
                               "author_role in ('person','group_admin','super_admin','system')")
    op.execute("delete from role_assignments where role = 'service_admin'")
    op.drop_index("role_one_service_admin", table_name="role_assignments")
    op.drop_constraint("role_scope", "role_assignments", type_="check")
    op.create_check_constraint(
        "role_scope", "role_assignments",
        "(role = 'super_admin' and group_id is null) or (role = 'group_admin' and group_id is not null)")
