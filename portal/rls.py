"""Row-level security (#350): which rows the database lets a session see, behind the API's own group checks.

The policies (migration 0044) read four settings, set at the start of every transaction from `session.info`:
the person's id, their groups (their own and those they administer), the address being signed in with, and a
bypass for super admins and cross-group work. They are transaction-local (`set_config(..., true)`), so a pooled
connection carries nothing over to the next request. A session nobody set up sees no group-scoped rows at all.

Using the bypass is audited (`rls.bypass`): a super admin's once per sign-in session and kind of request (the route,
never its IDs), and cross-group work outside a request (the worker, the status check, metrics) once per process.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import closing

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"  # as portal/main.py
KEY = "rls"
NOBODY = {"bypass": "", "groups": "", "user": "", "email": ""}
_SET = text("select set_config('portal.bypass', :bypass, true), set_config('portal.group_ids', :groups, true), "
            "set_config('portal.user_id', :user, true), set_config('portal.email', :email, true)")

# What has been audited already in this process: (sign-in session, route) for super admins, the reason for the rest
_audited: set = set()
_lock = threading.Lock()
_AUDITED_MAX = 20000


@event.listens_for(Session, "after_begin")
def _on_begin(session: Session, transaction, connection) -> None:
    values = session.info.get(KEY)
    if values is not None:
        connection.execute(_SET, values)


def _set(db: Session, **values: str) -> None:
    db.info[KEY] = {**NOBODY, **values}
    if db.in_transaction():  # otherwise the next transaction's start sets them
        db.connection().execute(_SET, db.info[KEY])


def identify(db: Session, *, user_id: uuid.UUID | None = None, email: str | None = None) -> None:
    """Only this person's own row: while signing in (by address), or before their groups are known (by id)."""
    _set(db, user=str(user_id) if user_id else "", email=email or "")


def as_person(db: Session, user, request: Request | None = None, sign_in_id: uuid.UUID | None = None) -> None:
    """The signed-in person's rows: their group's, and those of the groups they administer. Super admins work across
    groups, so they get the bypass."""
    if user.is_super_admin:
        _set(db, bypass="on", user=str(user.id))
        name = "other"
        if request is not None:
            # The route's pattern, never the path itself (it carries IDs); a router's routes lack the API's prefix
            route = getattr(request.scope.get("route"), "path", None) or "other"
            name = f"{request.method} {route if route.startswith(API_PREFIX) else API_PREFIX + route}"
        _audit_once(("super_admin", sign_in_id or user.id, name), request, actor=user, details={"route": name})
        return
    groups = sorted(str(g) for g in {user.group_id, *user.admin_group_ids()} if g)
    _set(db, user=str(user.id), groups=",".join(groups))


def as_token(db: Session, user) -> None:
    """A script with an API token (#518): its owner's rows and their own group's, never the bypass and never the groups
    they administer, whoever they are."""
    _set(db, user=str(user.id), groups=str(user.group_id) if user.group_id else "")


def bypass(db: Session, reason: str) -> None:
    """Cross-group work outside a person's request (the worker, the status check, metrics)."""
    _set(db, bypass="on")
    _audit_once(("system", reason), None, actor_email="system", details={"reason": reason})


def _audit_once(key: tuple, request: Request | None, **kw) -> None:
    with _lock:
        if key in _audited:
            return
        if len(_audited) >= _AUDITED_MAX:
            _audited.clear()
        _audited.add(key)
    # In a session of its own: the request may only read, and roll back at the end
    from portal import audit
    from portal.db import open_session
    try:
        with closing(open_session()) as s:
            audit.record(s, request, "rls.bypass", **kw)
            s.commit()
    except Exception:  # noqa: BLE001 - the request goes on; the next one tries again
        with _lock:
            _audited.discard(key)
        log.exception("couldn't audit a row-level security bypass")


def forget_audited() -> None:
    """For tests: audit each bypass again."""
    with _lock:
        _audited.clear()
