"""Large documents: "process now" on a waiting document

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-08 22:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('expedited_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('expedited_by', sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'expedited_by')
    op.drop_column('jobs', 'expedited_at')
