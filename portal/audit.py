"""Audit trail helpers. Every sign-in, admin action, file access by others and export is recorded."""
from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from portal.config import get_portal_settings
from portal.models import AuditEvent, User


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The caller's IP address, or None if it isn't a valid address (headers are untrusted input)."""
    s = get_portal_settings()
    if s.trust_proxy_headers:
        cf = request.headers.get(s.client_ip_header or "cf-connecting-ip")
        if cf:
            return _valid_ip(cf)
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return _valid_ip(fwd.split(",")[0])
    return _valid_ip(request.client.host if request.client else None)


def user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:400] if ua else None


def record(
    db: Session,
    request: Request | None,
    action: str,
    *,
    actor: User | None = None,
    actor_email: str | None = None,
    outcome: str = "success",
    target_type: str | None = None,
    target_id: Any = None,
    group_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    db.add(AuditEvent(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else actor_email,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        group_id=group_id,
        ip=client_ip(request) if request else None,
        user_agent=user_agent(request) if request else None,
        details=details or {},
    ))
