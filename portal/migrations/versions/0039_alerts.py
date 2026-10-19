"""Alerts: the portal worker's heartbeat, and the problems super admins have been told about (#334)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-16 23:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('worker_beats',
                    sa.Column('name', sa.String(40), primary_key=True),
                    sa.Column('at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('alert_states',
                    sa.Column('key', sa.String(40), primary_key=True),
                    sa.Column('detail', sa.Text(), nullable=False),
                    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_table('alert_states')
    op.drop_table('worker_beats')
