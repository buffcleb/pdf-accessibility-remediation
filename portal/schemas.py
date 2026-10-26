"""Response models shared by several routers."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session, object_session

from portal import services as svc
from portal.models import Group, GroupService, Service, User


class GroupBalance(BaseModel):
    """A group's pages of a service. A subgroup's (#431): its parent's pool, what the subgroup itself has used and
    holds of it, and what is left to it (the parent's left, within the subgroup's limit)."""
    pool_pages: int
    used_pages: int
    reserved_pages: int
    remaining_pages: int
    cap_pages: int | None = None  # a subgroup's limit on its parent's pool (None: no limit)
    shared: bool = False          # a subgroup's: the pool is its parent's


class GroupServiceOut(BaseModel):
    """A group's terms for one service."""
    key: str
    label: str
    kind: str
    globally_enabled: bool
    external: bool
    metered: bool
    enabled: bool              # the group's own switch
    available: bool            # globally on, group on, and not blocked by local-only
    all_users: bool
    is_default: bool
    default_user_cap: int | None
    balance: GroupBalance
    people_with_access: int = 0  # active members who can use it (everyone, or those given it)
    own_limits: int = 0          # active members with a limit of their own (the exceptions to the default)
    kinds: list[dict] = []       # AI levels: kinds of document beyond General, as {key, label} (never models)


class UserServiceOut(BaseModel):
    """A user's access to one service."""
    key: str
    label: str
    kind: str
    granted: bool              # individually, or through the group granting it to everyone
    granted_individually: bool
    available: bool            # usable now (all switches on, granted)
    cap_pages: int | None      # explicit cap; None = group default
    effective_cap: int | None  # None = no cap (the pools still apply)
    used_pages: int
    reserved_pages: int
    remaining_pages: int | None  # None = unlimited


class GroupEmailOut(BaseModel):
    email: str
    note: str | None
    added_by: str | None
    created_at: datetime
    has_account: bool  # they've signed in (removing the address then doesn't remove the account)


class GroupRef(BaseModel):
    id: uuid.UUID
    name: str
    local_only: bool = False  # a parent's: its subgroups can't turn local-only off while it's on


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    domains: list[str]
    emails: list[GroupEmailOut] = []  # people allowed in by their address (super admins change them)
    local_only: bool
    require_approval: bool
    ai_mode: str  # choose | fixed
    ai_level: str | None  # fixed: the AI level everyone's documents use
    ai_kind: str | None  # fixed: its kind of document (None: General)
    show_external: bool  # show people which options send documents to an external provider
    ai_describe: bool | None = None  # "Describe with AI" for its people; None follows the service default
    api_tokens_enabled: bool = False  # its people may make API tokens for scripts (#518)
    pdfua_choice: bool = False  # its people may choose PDF/UA-2 or Both (#445), when the service offers it
    pdfua_default: str = "1"  # what's preselected: "1", "2" or "both"
    parent: GroupRef | None = None  # a subgroup's parent (#431)
    subgroups: list[GroupRef] = []  # a parent's subgroups, by name
    inherits: list[str] = []  # a subgroup's settings taken from its parent (portal/inherit.py); the rest its own
    pending_members: int  # people waiting for approval (in its subgroups too)
    help_active: int  # help requests from the group still being worked on
    # The standard service's terms (kept for screens that show one pool)
    default_user_cap: int | None
    over_quota_message: str | None
    balance: GroupBalance
    services: list[GroupServiceOut]
    created_at: datetime


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    group_id: uuid.UUID | None
    group_name: str | None
    status: str
    approval: str  # pending, approved or declined
    approval_at: datetime | None
    approval_by: str | None
    removed_at: datetime | None = None  # their data removed on request (#338)
    decline_reason: str | None
    auth_method: str
    roles: list[dict]
    # The standard service's terms (kept for screens that show one pool)
    cap_pages: int | None          # explicit cap; None = group default
    effective_cap: int | None      # None = unlimited (pool still applies)
    used_pages: int
    reserved_pages: int
    remaining_pages: int | None
    services: list[UserServiceOut]
    created_at: datetime
    last_login_at: datetime | None
    ai_describe: bool | None = None  # "Describe with AI" set for this person; None follows their group


class MeOut(UserOut):
    is_super_admin: bool
    is_service_admin: bool = False  # #564: helps run every group
    permissions: list[str] = []  # what their roles let them do (portal/permissions.py)
    admin_of: list[uuid.UUID]
    can_submit: bool  # approved, or an admin
    pending_approvals: int  # people waiting in the groups this person administers
    help_unread: int  # their own help requests with something new
    help_needs_us: int  # support (super admins, service admins): help requests waiting on support
    can_describe: bool = False  # "Describe with AI" in the tag manager, as the three levels work out


def balance_of(gs: GroupService | None) -> GroupBalance:
    if gs is None:
        return GroupBalance(pool_pages=0, used_pages=0, reserved_pages=0, remaining_pages=0)
    g = gs.group
    if g.parent_id is not None:
        pool = g.pool.service(gs.service_key)
        return GroupBalance(pool_pages=pool.pool_pages if pool else 0, used_pages=gs.used_pages,
                            reserved_pages=gs.reserved_pages, remaining_pages=max(0, (svc.pool_left(g, gs.service_key)
                                                                                    or (0, 0))[1]),
                            cap_pages=gs.cap_pages, shared=True)
    return GroupBalance(pool_pages=gs.pool_pages, used_pages=gs.used_pages, reserved_pages=gs.reserved_pages,
                        remaining_pages=max(0, gs.pool_pages - gs.used_pages - gs.reserved_pages))


def group_balance(g: Group, key: str = svc.STANDARD) -> GroupBalance:
    return balance_of(g.service(key))


def _member_counts(g: Group) -> tuple[int, dict[str, tuple[int, int]]]:
    """(active members, {service: (given individually, with their own limit)}) for the group."""
    from sqlalchemy import case, func, select

    from portal.models import UserService
    db = object_session(g)
    if db is None:
        return 0, {}
    members = db.scalar(select(func.count()).select_from(User).where(User.group_id == g.id, User.status == "active")) or 0
    rows = db.execute(
        select(UserService.service_key,
               func.sum(case((UserService.granted, 1), else_=0)),
               func.sum(case((UserService.cap_pages.is_not(None), 1), else_=0)))
        .join(User, User.id == UserService.user_id)
        .where(User.group_id == g.id, User.status == "active")
        .group_by(UserService.service_key)).all()
    return members, {k: (int(a or 0), int(b or 0)) for k, a, b in rows}


def _kinds_for(db: Session, g: Group, level: str) -> list[dict]:
    """A level's kinds by name, with "Let AI decide" first when the group can use the classifier."""
    ks = [{"key": k.key, "label": k.label} for k in svc.kinds(db, level)]
    if ks and svc.classifier_for(db, g) is not None:
        ks.insert(0, {"key": svc.AUTO, "label": "Let AI decide"})
    return ks


def group_services_out(g: Group, services: list[Service]) -> list[GroupServiceOut]:
    members, counts = _member_counts(g)
    db = object_session(g)
    out = []
    for s in services:
        gs = g.service(s.key)
        enabled = bool(gs and gs.enabled)
        parent_on = g.parent_id is None or bool((p := g.pool.service(s.key)) and p.enabled)  # #431
        granted, own = counts.get(s.key, (0, 0))
        out.append(GroupServiceOut(
            key=s.key, label=s.label, kind=s.kind, globally_enabled=s.enabled, external=s.external,
            metered=s.metered, enabled=enabled,
            available=s.enabled and enabled and parent_on and not (s.external and g.local_only),
            all_users=bool(gs and gs.all_users), is_default=bool(gs and gs.is_default),
            default_user_cap=gs.default_user_cap if gs else None, balance=balance_of(gs),
            people_with_access=(members if gs and gs.all_users else granted) if enabled else 0, own_limits=own,
            kinds=_kinds_for(db, g, s.key) if s.kind == "ai_tier" and db is not None else []))
    return out


def group_out(g: Group, services: list[Service] | None = None) -> GroupOut:
    std = g.service(svc.STANDARD)
    services = services if services is not None else _services_of(g)
    return GroupOut(id=g.id, name=g.name, status=g.status, domains=[d.domain for d in g.domains],
                    emails=_emails_of(g),
                    local_only=g.local_only, require_approval=g.require_approval, ai_mode=g.ai_mode, ai_level=g.ai_level,
                    ai_kind=g.ai_kind, show_external=g.show_external, ai_describe=g.ai_describe,
                    api_tokens_enabled=g.api_tokens_enabled, pdfua_choice=g.pdfua_choice,
                    pdfua_default=g.pdfua_default or "1", **_family_of(g),
                    pending_members=_pending_in(g), help_active=_help_active_in(g), default_user_cap=std.default_user_cap if std else None,
                    over_quota_message=g.over_quota_message, balance=balance_of(std),
                    services=group_services_out(g, services), created_at=g.created_at)


def _emails_of(g: Group) -> list[GroupEmailOut]:
    if not g.emails:
        return []
    from sqlalchemy import select
    db = object_session(g)
    known = set(db.scalars(select(User.email).where(User.email.in_([e.email for e in g.emails])))) if db else set()
    return [GroupEmailOut(email=e.email, note=e.note, added_by=e.added_by, created_at=e.created_at,
                          has_account=e.email in known) for e in g.emails]


def _family_of(g: Group) -> dict:
    """A group's parent and subgroups (#431), as GroupOut names them."""
    from sqlalchemy import select
    db = object_session(g)
    if db is None:
        return {}
    parent = db.get(Group, g.parent_id) if g.parent_id else None
    subs = [] if g.parent_id else db.execute(
        select(Group.id, Group.name).where(Group.parent_id == g.id).order_by(Group.name)).all()
    from portal.inherit import inherited
    return {"parent": GroupRef(id=parent.id, name=parent.name, local_only=parent.local_only) if parent else None,
            "subgroups": [GroupRef(id=i, name=n) for i, n in subs], "inherits": inherited(g)}


def _pending_in(g: Group) -> int:
    """People waiting in the group and its subgroups (#431), whose admins can approve them all."""
    from portal.approval import pending_count
    from portal.models import group_family
    db = object_session(g)
    return pending_count(db, group_family(db, g.id)) if db is not None else 0


def _help_active_in(g: Group) -> int:
    from sqlalchemy import func, select

    from portal.models import HelpRequest, group_family
    db = object_session(g)
    return (db.scalar(select(func.count()).select_from(HelpRequest).where(
        HelpRequest.group_id.in_(group_family(db, g.id)),
        HelpRequest.status.in_(("open", "in_progress", "waiting")))) or 0) if db else 0


def _services_of(obj) -> list[Service]:
    """Every service, from the session the object belongs to."""
    db = object_session(obj)
    return svc.all_services(db) if db is not None else []


def effective_cap(u: User, key: str = svc.STANDARD) -> int | None:
    return svc.effective_cap(u, key)


def user_remaining(u: User, key: str = svc.STANDARD) -> int | None:
    """Pages this user can still submit to a service: the smallest of their cap and the pools."""
    if u.group is None:
        return None
    s = object_session(u).get(Service, key) if object_session(u) else None
    if s is None:
        return 0
    return svc.remaining(u, key, s)


def user_services_out(u: User, services: list[Service]) -> list[UserServiceOut]:
    out = []
    for s in services:
        us = u.service(s.key)
        g = u.group
        gs = g.service(s.key) if g else None
        available = bool(s.enabled and gs and gs.enabled and not (s.external and g.local_only)
                         and svc.granted(u, s.key))
        out.append(UserServiceOut(
            key=s.key, label=s.label, kind=s.kind, granted=svc.granted(u, s.key),
            granted_individually=bool(us and us.granted), available=available,
            cap_pages=us.cap_pages if us else None, effective_cap=svc.effective_cap(u, s.key) if g else None,
            used_pages=us.used_pages if us else 0, reserved_pages=us.reserved_pages if us else 0,
            remaining_pages=svc.remaining(u, s.key, s) if g else None))
    return out


def user_out(u: User, services: list[Service] | None = None) -> UserOut:
    services = services if services is not None else _services_of(u)
    std = u.service(svc.STANDARD)
    std_service = next((s for s in services if s.key == svc.STANDARD), None)
    return UserOut(
        id=u.id, email=u.email, name=u.name, group_id=u.group_id, group_name=u.group.name if u.group else None,
        status=u.status, approval=u.approval, approval_at=u.approval_at, approval_by=u.approval_by,
        removed_at=u.removed_at,
        decline_reason=u.decline_reason, auth_method=u.auth_method,
        roles=[{"role": r.role, "group_id": r.group_id} for r in u.roles],
        cap_pages=std.cap_pages if std else None, effective_cap=effective_cap(u) if u.group else None,
        used_pages=std.used_pages if std else 0, reserved_pages=std.reserved_pages if std else 0,
        remaining_pages=svc.remaining(u, svc.STANDARD, std_service) if (u.group and std_service) else None,
        services=user_services_out(u, services),
        created_at=u.created_at, last_login_at=u.last_login_at, ai_describe=u.ai_describe)


def me_out(db: Session, u: User) -> MeOut:
    from portal import ai_describe, permissions
    base = user_out(u, svc.all_services(db)).model_dump()
    from portal.approval import pending_count
    groups = None if u.across_groups else list(u.admin_group_ids())
    from sqlalchemy import select

    from portal.models import HelpRequest
    mine = db.scalars(select(HelpRequest).where(HelpRequest.user_id == u.id)).all()
    needs_us = 0
    if permissions.has(u, permissions.HELP):
        needs_us = sum(1 for r in db.scalars(select(HelpRequest).where(
            HelpRequest.status.in_(("open", "in_progress")), HelpRequest.saved.is_(False))) if r.needs_staff)
    return MeOut(**base, is_super_admin=u.is_super_admin, is_service_admin=u.is_service_admin,
                 permissions=sorted(permissions.of(u)), admin_of=sorted(u.admin_group_ids(), key=str),
                 can_submit=u.approved, pending_approvals=pending_count(db, groups) if (u.across_groups or groups) else 0,
                 help_unread=sum(1 for r in mine if r.unread_for_person), help_needs_us=needs_us,
                 can_describe=ai_describe.allowed(db, u))
