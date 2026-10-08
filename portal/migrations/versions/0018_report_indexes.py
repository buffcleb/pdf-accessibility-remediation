"""Indexes for the live reports: date ranges across all groups, and the audit log by group

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-08 18:00:00
"""
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('jobs_created_idx', 'jobs', ['created_at'])
    op.create_index('ledger_created_idx', 'quota_ledger', ['created_at'])
    op.create_index('audit_group_created_idx', 'audit_events', ['group_id', 'created_at'])
    op.create_index('help_created_idx', 'help_requests', ['created_at'])


def downgrade() -> None:
    op.drop_index('help_created_idx', table_name='help_requests')
    op.drop_index('audit_group_created_idx', table_name='audit_events')
    op.drop_index('ledger_created_idx', table_name='quota_ledger')
    op.drop_index('jobs_created_idx', table_name='jobs')
