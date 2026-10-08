"""Adobe Autotag as a processing service: which engine route a service uses, and the "adobe" service
(switched off, metered, confirm before sending, with a notice: documents go to Adobe)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('engine_route', sa.String(32), nullable=False, server_default='standard'))
    op.create_check_constraint('service_engine_route', 'services', "engine_route in ('standard','adobe')")
    op.execute("""
        insert into services (key, kind, label, description, enabled, external, notice_enabled, notice_text, metered,
                              used_pages, reserved_pages, confirm_before_send, engine_route, sort)
        values ('adobe', 'processing', 'Adobe Autotag',
                'Tagged by Adobe PDF Services. You get the file exactly as Adobe returns it, checked by this service.',
                false, true, true,
                'Documents are sent to Adobe PDF Services for tagging and deleted from Adobe after processing. '
                'Adobe doesn''t write alternate text for images.',
                true, 0, 0, true, 'adobe', 20)
        on conflict (key) do nothing
    """)


def downgrade() -> None:
    op.execute("delete from services where key = 'adobe' and used_pages = 0 and reserved_pages = 0")
    op.drop_constraint('service_engine_route', 'services', type_='check')
    op.drop_column('services', 'engine_route')
