"""Services: the most documents processed at once (so a fixed resource, like a local GPU, isn't swamped)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-09 22:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('max_at_once', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('services', 'max_at_once')
