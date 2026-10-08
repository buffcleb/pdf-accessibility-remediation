from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from pydantic import BaseModel, field_serializer
from sqlalchemy import Select, select

from portal.models import AuditEvent


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    group_id: uuid.UUID | None
    ip: str | IPv4Address | IPv6Address | None
    details: dict

    @field_serializer("ip")
    def _ip(self, v):
        return str(v) if v is not None else None


def audit_query(*, action: str | None, actor_id: uuid.UUID | None, since: datetime | None,
                until: datetime | None, before_id: int | None) -> Select:
    """Newest first; page with before_id = the last id of the previous page."""
    stmt = select(AuditEvent)
    if action:
        # "login" matches login, "group." matches every group action
        stmt = stmt.where(AuditEvent.action.startswith(action) if action.endswith(".") else AuditEvent.action == action)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.created_at < until)
    if before_id:
        stmt = stmt.where(AuditEvent.id < before_id)
    return stmt.order_by(AuditEvent.id.desc())


class LedgerOut(BaseModel):
    id: int
    created_at: datetime
    group_id: uuid.UUID | None
    service_key: str
    scope: str
    user_id: uuid.UUID | None
    job_id: uuid.UUID | None
    kind: str
    pages: int
    ticket_ref: str | None
    ticket_url: str | None
    override: bool
    comment: str | None
    actor_email: str | None
    pool_after: int
    used_after: int
    reserved_after: int
