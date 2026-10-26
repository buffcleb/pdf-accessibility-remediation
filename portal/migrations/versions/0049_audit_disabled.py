"""Check only can be turned off for an AI level: services.audit_ai 'disabled' (#586)

With it, a check with that level is refused, and the upload form doesn't offer Check only while it's chosen. The
downgrade turns such levels' checks to 'off' (made without AI), the nearest setting before this.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-23 18:00:00
"""
from alembic import op

revision = '0049'
down_revision = '0048'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('service_audit_ai', 'services', type_='check')
    op.create_check_constraint('service_audit_ai', 'services', "audit_ai in ('free','charge','off','disabled')")


def downgrade() -> None:
    op.execute("UPDATE services SET audit_ai = 'off' WHERE audit_ai = 'disabled'")
    op.drop_constraint('service_audit_ai', 'services', type_='check')
    op.create_check_constraint('service_audit_ai', 'services', "audit_ai in ('free','charge','off')")
