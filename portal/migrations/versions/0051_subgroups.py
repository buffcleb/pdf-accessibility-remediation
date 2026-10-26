"""Subgroups (#431): a group can sit under one other group, one level deep.

groups.parent_id names the parent. A group can't be its own parent, a subgroup can't have subgroups, and a group
with subgroups can't be put under another: a trigger refuses both, as a check can't see other rows.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-25 23:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0051'
down_revision = '0050'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('parent_id', postgresql.UUID(as_uuid=True),
                                      sa.ForeignKey('groups.id', ondelete='RESTRICT'), nullable=True))
    op.create_index('ix_groups_parent_id', 'groups', ['parent_id'])
    op.create_check_constraint('group_not_own_parent', 'groups', 'parent_id is null or parent_id <> id')
    op.execute("""
        create function portal_group_one_level() returns trigger language plpgsql as $$
        begin
            if new.parent_id is not null then
                if exists (select 1 from groups g where g.id = new.parent_id and g.parent_id is not null) then
                    raise exception 'a subgroup can''t have subgroups' using errcode = 'check_violation';
                end if;
                if exists (select 1 from groups g where g.parent_id = new.id) then
                    raise exception 'a group with subgroups can''t be a subgroup' using errcode = 'check_violation';
                end if;
            end if;
            return new;
        end $$;
    """)
    op.execute("""
        create trigger groups_one_level before insert or update of parent_id on groups
        for each row execute function portal_group_one_level();
    """)


def downgrade() -> None:
    op.execute('drop trigger if exists groups_one_level on groups')
    op.execute('drop function if exists portal_group_one_level()')
    op.drop_constraint('group_not_own_parent', 'groups', type_='check')
    op.drop_index('ix_groups_parent_id', table_name='groups')
    op.drop_column('groups', 'parent_id')
