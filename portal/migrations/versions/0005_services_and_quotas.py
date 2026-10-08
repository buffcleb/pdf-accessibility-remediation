"""Services with per-service opt-in and page quotas at global, group and user level

Moves the group page pool and the user caps and usage into the "standard" service, so later services
(Adobe, AI tiers) use the same accounting.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 09:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'services',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('external', sa.Boolean(), nullable=False),
        sa.Column('notice_enabled', sa.Boolean(), nullable=False),
        sa.Column('notice_text', sa.Text(), nullable=True),
        sa.Column('metered', sa.Boolean(), nullable=False),
        sa.Column('pool_pages', sa.BigInteger(), nullable=True),
        sa.Column('used_pages', sa.BigInteger(), nullable=False),
        sa.Column('reserved_pages', sa.BigInteger(), nullable=False),
        sa.Column('daily_cap_pages', sa.BigInteger(), nullable=True),
        sa.Column('max_pages_per_job', sa.Integer(), nullable=True),
        sa.Column('confirm_before_send', sa.Boolean(), nullable=False),
        sa.Column('gateway_model', sa.String(200), nullable=True),
        sa.Column('sort', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind in ('processing','ai_tier')", name='service_kind'),
        sa.CheckConstraint("used_pages >= 0 and reserved_pages >= 0 and (pool_pages is null or pool_pages >= 0)",
                           name='service_pages_nonneg'),
    )
    op.create_table(
        'group_services',
        sa.Column('group_id', sa.UUID(), sa.ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('service_key', sa.String(64), sa.ForeignKey('services.key', ondelete='CASCADE'), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('all_users', sa.Boolean(), nullable=False),
        sa.Column('pool_pages', sa.BigInteger(), nullable=False),
        sa.Column('used_pages', sa.BigInteger(), nullable=False),
        sa.Column('reserved_pages', sa.BigInteger(), nullable=False),
        sa.Column('default_user_cap', sa.BigInteger(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.CheckConstraint("pool_pages >= 0 and used_pages >= 0 and reserved_pages >= 0",
                           name='group_service_nonneg'),
    )
    op.create_table(
        'user_services',
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('service_key', sa.String(64), sa.ForeignKey('services.key', ondelete='CASCADE'), primary_key=True),
        sa.Column('granted', sa.Boolean(), nullable=False),
        sa.Column('cap_pages', sa.BigInteger(), nullable=True),
        sa.Column('used_pages', sa.BigInteger(), nullable=False),
        sa.Column('reserved_pages', sa.BigInteger(), nullable=False),
        sa.CheckConstraint("used_pages >= 0 and reserved_pages >= 0", name='user_service_nonneg'),
    )

    # The standard service: on for everyone, with today's pools, caps and usage
    op.execute("""
        insert into services (key, kind, label, description, enabled, external, notice_enabled, metered,
                              used_pages, reserved_pages, confirm_before_send, sort)
        select 'standard', 'processing', 'Standard',
               'Remediation by this service''s own engine, on this service''s own servers.',
               true, false, false, true,
               coalesce(sum(used_pages), 0), coalesce(sum(reserved_pages), 0), false, 0
        from groups
    """)
    op.execute("""
        insert into group_services (group_id, service_key, enabled, all_users, pool_pages, used_pages,
                                    reserved_pages, default_user_cap, is_default)
        select id, 'standard', true, true, pool_pages, used_pages, reserved_pages, default_user_cap, true
        from groups
    """)
    op.execute("""
        insert into user_services (user_id, service_key, granted, cap_pages, used_pages, reserved_pages)
        select id, 'standard', true, cap_pages, used_pages, reserved_pages from users
    """)

    op.add_column('groups', sa.Column('local_only', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('groups') as b:
        b.drop_constraint('group_pages_nonneg', type_='check')
        for c in ('pool_pages', 'used_pages', 'reserved_pages', 'default_user_cap'):
            b.drop_column(c)
    for c in ('cap_pages', 'used_pages', 'reserved_pages'):
        op.drop_column('users', c)

    op.add_column('jobs', sa.Column('processing', sa.String(64), nullable=False, server_default='standard'))
    op.add_column('jobs', sa.Column('ai_tier', sa.String(64), nullable=True))
    op.add_column('jobs', sa.Column('charged_services', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column('jobs', sa.Column('external_services', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column('jobs', sa.Column('cost', postgresql.JSONB(), nullable=True))
    op.execute("""update jobs set charged_services = '["standard"]'::jsonb where pages_charged > 0""")
    op.drop_constraint('job_status', 'jobs', type_='check')
    op.create_check_constraint('job_status', 'jobs',
                               "status in ('held','queued','submitted','processing','completed','failed','canceled')")

    # Ledger: entries now belong to a service, on its global pool or a group's pool. Existing entries are
    # the standard service's group entries (new columns get their defaults; no row is updated).
    op.add_column('quota_ledger', sa.Column('service_key', sa.String(64), nullable=False, server_default='standard'))
    op.add_column('quota_ledger', sa.Column('scope', sa.String(8), nullable=False, server_default='group'))
    op.alter_column('quota_ledger', 'group_id', nullable=True)
    op.create_check_constraint('ledger_scope', 'quota_ledger',
                               "(scope = 'global' and group_id is null) or (scope = 'group' and group_id is not null)")
    op.create_index('ledger_service_created_idx', 'quota_ledger', ['service_key', 'created_at'])


def downgrade() -> None:
    raise NotImplementedError("Services can't be folded back into a single page pool automatically.")
