"""Super-admin corrections of a person's page usage, as ledger entries of kind 'correct'

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 12:00:00
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ledger_kind', 'quota_ledger', type_='check')
    op.create_check_constraint('ledger_kind', 'quota_ledger',
                               "kind in ('topup','adjust','reserve','commit','release','correct')")
    op.create_check_constraint('ledger_correct', 'quota_ledger',
                               "kind <> 'correct' or (user_id is not null and comment is not null)")


def downgrade() -> None:
    op.drop_constraint('ledger_correct', 'quota_ledger', type_='check')
    op.drop_constraint('ledger_kind', 'quota_ledger', type_='check')
    op.create_check_constraint('ledger_kind', 'quota_ledger',
                               "kind in ('topup','adjust','reserve','commit','release')")
