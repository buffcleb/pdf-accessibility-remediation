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


def _from_trusted_proxy(peer: str | None, trusted: str) -> bool:
    if not trusted.strip():
        return True
    try:
        addr = ipaddress.ip_address((peer or "").strip())
    except ValueError:
        return False
    for net in trusted.split(","):
        try:
            if net.strip() and addr in ipaddress.ip_network(net.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str | None:
    """The caller's IP address, or None if it isn't a valid address (headers are untrusted input). The proxy headers
    are read only on a connection from a trusted proxy (PORTAL_TRUSTED_PROXIES)."""
    s = get_portal_settings()
    peer = request.client.host if request.client else None
    if s.trust_proxy_headers and _from_trusted_proxy(peer, s.trusted_proxies):
        name = (s.client_ip_header or "cf-connecting-ip").lower()
        if name != "x-forwarded-for":
            own = request.headers.get(name)
            if own:
                return _valid_ip(own)
        # X-Forwarded-For grows from the left: a caller can put anything at the front, and each proxy adds the
        # address it saw at the end. So count back from the end, one place per trusted proxy; never the first.
        hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
        if hops:
            back = max(1, getattr(s, "trusted_proxy_hops", 1))
            return _valid_ip(hops[-back] if len(hops) >= back else hops[0])
    return _valid_ip(peer)


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
