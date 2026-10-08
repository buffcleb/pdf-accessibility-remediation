"""Authentication and authorization dependencies.

Every route declares who may call it through one of these. Access is denied by default: a route
without `current_principal` (or a stricter dependency) is public, and only the auth routes and the
health check are.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from portal.db import get_db
from portal.errors import ApiError, forbidden, not_found, unauthorized
from portal.models import AuthSession, Group, User
from portal.security.tokens import decode_access_token


@dataclass
class Principal:
    user: User
    session: AuthSession

    @property
    def is_super_admin(self) -> bool:
        return self.user.is_super_admin

    def can_admin_group(self, group_id: uuid.UUID) -> bool:
        return self.is_super_admin or group_id in self.user.admin_group_ids()


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    return token.strip()


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    try:
        claims = decode_access_token(_bearer(request))
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise unauthorized("Your session has expired. Sign in again.") from None

    # Checking the session on every request makes sign-out and disabling take effect at once.
    session = db.get(AuthSession, session_id)
    now = datetime.now(timezone.utc)
    if session is None or session.user_id != user_id or session.revoked_at is not None or session.expires_at <= now:
        raise unauthorized("Your session has ended. Sign in again.")
    user = session.user
    if user.status != "active":
        raise unauthorized("Your account has been disabled.")
    if not user.is_super_admin:
        group = db.get(Group, user.group_id) if user.group_id else None
        if group is None or group.status != "active":
            raise unauthorized("Your group has been disabled.")
    request.state.principal = session
    return Principal(user=user, session=session)


def require_super_admin(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_super_admin:
        raise forbidden()
    return p


def group_for_admin(group_id: uuid.UUID, p: Principal, db: Session) -> Group:
    """The group, if the caller administers it. Others get 404 so group IDs can't be probed."""
    group = db.get(Group, group_id)
    if group is None or not p.can_admin_group(group_id):
        raise not_found("Group")
    return group


def require_any_admin(p: Principal = Depends(current_principal)) -> Principal:
    if not (p.is_super_admin or p.user.admin_group_ids()):
        raise forbidden()
    return p


__all__ = ["Principal", "current_principal", "require_super_admin", "require_any_admin", "group_for_admin", "ApiError"]
