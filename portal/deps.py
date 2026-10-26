"""Authentication and authorization dependencies.

Every route declares who may call it through one of these. Access is denied by default: a route
without `current_principal` (or a stricter dependency) is public, and only the auth routes and the
health check are.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timezone

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from portal import api_tokens, permissions, rls
from portal.db import get_db
from portal.errors import ApiError, forbidden, not_found, unauthorized
from portal.models import ApiToken, AuthSession, Group, User
from portal.security.tokens import decode_access_token


@dataclass
class Principal:
    user: User
    session: AuthSession | None = None
    token: ApiToken | None = None  # a script's API token (#518): its owner's own documents only

    @property
    def is_super_admin(self) -> bool:
        # A token never acts as an admin, whoever owns it: it reaches its owner's own documents
        return self.token is None and self.user.is_super_admin

    def can(self, permission: str) -> bool:
        """What the person's roles let them do (portal/permissions.py); never with a token."""
        return self.token is None and permissions.has(self.user, permission)

    def can_admin_group(self, group_id: uuid.UUID) -> bool:
        return self.token is None and (self.can(permissions.ALL_GROUPS) or group_id in self.user.admin_group_ids())


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    return token.strip()


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """A signed-in person. API tokens are refused here: only the document routes that ask for one
    (`document_principal`) take them."""
    bearer = _bearer(request)
    if api_tokens.is_token(bearer):
        raise ApiError(403, "token_not_allowed", "API tokens can only submit, check and download documents.")
    return _session_principal(request, db, bearer)


@lru_cache
def document_principal(scope: str):
    """For the document routes a script may use (#518): a signed-in person, or an API token that may do `scope`
    (submit, status or download)."""
    def dependency(request: Request, db: Session = Depends(get_db)) -> Principal:
        bearer = _bearer(request)
        if not api_tokens.is_token(bearer):
            return _session_principal(request, db, bearer)
        user, token = api_tokens.authenticate(db, request, bearer)
        api_tokens.require(token, scope)
        request.state.api_token = token
        return Principal(user=user, token=token)
    return dependency


def _session_principal(request: Request, db: Session, bearer: str) -> Principal:
    try:
        claims = decode_access_token(bearer)
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise unauthorized("Your session has expired. Sign in again.") from None

    # Checking the session on every request makes sign-out and disabling take effect at once. Until it's checked,
    # the database shows only the person's own row (row-level security, #350); then their groups' rows.
    rls.identify(db, user_id=user_id)
    session = db.get(AuthSession, session_id)
    now = datetime.now(timezone.utc)
    if session is None or session.user_id != user_id or session.revoked_at is not None or session.expires_at <= now:
        raise unauthorized("Your session has ended. Sign in again.")
    user = session.user
    if user.status != "active":
        raise unauthorized("Your account has been disabled.")
    if not user.across_groups:
        group = db.get(Group, user.group_id) if user.group_id else None
        if group is None or group.status != "active":
            raise unauthorized("Your group has been disabled.")
    rls.as_person(db, user, request, session.id)
    request.state.principal = session
    return Principal(user=user, session=session)


def require_super_admin(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_super_admin:
        raise forbidden()
    return p


@lru_cache
def require(permission: str):
    """For a route only those with this permission may use (portal/permissions.py): super admins, and service admins
    for theirs."""
    def dependency(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(permission):
            raise forbidden()
        return p
    dependency.permission = permission  # for the route-by-role test
    return dependency


def group_for_admin(group_id: uuid.UUID, p: Principal, db: Session) -> Group:
    """The group, if the caller administers it. Others get 404 so group IDs can't be probed."""
    group = db.get(Group, group_id)
    if group is None or not p.can_admin_group(group_id):
        raise not_found("Group")
    return group


def require_any_admin(p: Principal = Depends(current_principal)) -> Principal:
    """Any admin: one who may see every group's reports (super admins, service admins), or of a group."""
    if not (p.can(permissions.REPORTS) or p.user.admin_group_ids()):
        raise forbidden()
    return p


__all__ = ["Principal", "current_principal", "document_principal", "require_super_admin", "require", "require_any_admin",
           "group_for_admin", "ApiError"]
