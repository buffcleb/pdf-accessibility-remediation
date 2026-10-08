"""Per AI tier: whether the AI corrects OCR words Tesseract was unsure of (on by default)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07 20:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('allow_ocr_ai', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column('services', 'allow_ocr_ai')
