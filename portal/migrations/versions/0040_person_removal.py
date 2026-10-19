"""People: removed on request, their records kept without their identity (#338)

The audit log and the page ledger stay append-only, with one narrow exception: while a person's removal runs (the
session setting portal.anonymizing = on), a row may change, but only in the columns that say who and from where. The
trigger compares the whole row less those columns, so an action, an amount or a time can never change.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-17 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('removed_by', sa.String(320), nullable=True))
    op.execute("""
        create or replace function audit_events_no_update() returns trigger language plpgsql as $$
        begin
            if current_setting('portal.anonymizing', true) = 'on'
               and (to_jsonb(new) - tg_argv) = (to_jsonb(old) - tg_argv) then
                return new;
            end if;
            raise exception '% is append-only', tg_table_name;
        end $$;
        drop trigger if exists audit_events_no_update on audit_events;
        create trigger audit_events_no_update before update on audit_events
            for each row execute function audit_events_no_update('actor_email', 'ip', 'user_agent', 'details');
        drop trigger if exists quota_ledger_no_update on quota_ledger;
        create trigger quota_ledger_no_update before update on quota_ledger
            for each row execute function audit_events_no_update('actor_email', 'ip');
    """)


def downgrade() -> None:
    op.execute("""
        create or replace function audit_events_no_update() returns trigger language plpgsql as $$
        begin raise exception 'audit_events is append-only'; end $$;
        drop trigger if exists audit_events_no_update on audit_events;
        create trigger audit_events_no_update before update on audit_events
            for each row execute function audit_events_no_update();
        drop trigger if exists quota_ledger_no_update on quota_ledger;
        create trigger quota_ledger_no_update before update on quota_ledger
            for each row execute function audit_events_no_update();
    """)
    op.drop_column('users', 'removed_by')
    op.drop_column('users', 'removed_at')
