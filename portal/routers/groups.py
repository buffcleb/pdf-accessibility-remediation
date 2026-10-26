"""Group-admin routes, scoped to groups the caller administers (super admins and service admins may use them for any
group)."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session, aliased

from portal import approval, audit, inherit, paging, quota, services
from portal.db import get_db
from portal.deps import Principal, current_principal, group_for_admin
from portal.errors import ApiError, not_found
from portal.models import AuditEvent, Group, GroupDomain, RoleAssignment, User, UserService, group_family
from portal.paging import Page
from portal.permissions import ALL_GROUPS
from portal.routers.admin import new_group, revoke_sessions, subgroup_parent
from portal.routers.common import AuditOut, audit_query, by_whom
from portal.schemas import GroupOut, UserOut, group_out, user_out

router = APIRouter(prefix="/groups", tags=["group admin"])


class MemberUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(active|disabled)$")
    # Explicit page cap for the standard service; null resets the user to the group default
    cap_pages: int | None = Field(None, ge=0, le=10_000_000)
    # "Describe with AI" for this person; null follows the group
    ai_describe: bool | None = None
    # Move them between a group and its subgroups (#431), for admins of both; elsewhere is a super admin's move
    group_id: uuid.UUID | None = None


@router.get("", response_model=list[GroupOut])
def my_admin_groups(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Groups the caller administers (all groups for super admins and service admins)."""
    stmt = select(Group).order_by(Group.name)
    if not p.can(ALL_GROUPS):
        ids = p.user.admin_group_ids()
        if not ids:
            return []
        stmt = stmt.where(Group.id.in_(ids))
    return [group_out(g) for g in db.scalars(stmt)]


GROUP_SORTS = {"name": func.lower(Group.name), "created": Group.created_at}


@router.get("/browse", response_model=Page[GroupOut])
def browse_groups(q: str = Query("", max_length=200), status: str | None = Query(None, pattern="^(active|disabled)$"),
                  sort: str = Query("name", max_length=20), page: int = Query(1, ge=1, le=100_000),
                  page_size: int = Query(25, ge=1, le=200), p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db)):
    """The groups the caller administers (every group for super admins and service admins), a page at a time (#514): search by name or
    email domain, filter by status, sort by name or when it was made."""
    stmt = select(Group)
    if not p.can(ALL_GROUPS):
        stmt = stmt.where(Group.id.in_(p.user.admin_group_ids() or [uuid.UUID(int=0)]))
    if q.strip():
        pattern = paging.like(q)
        stmt = stmt.where(or_(func.lower(Group.name).like(pattern, escape="\\"),
                              Group.id.in_(select(GroupDomain.group_id).where(GroupDomain.domain.like(pattern, escape="\\")))))
    if status:
        stmt = stmt.where(Group.status == status)
    order = paging.order_by(sort, GROUP_SORTS, Group.id)
    if sort.lstrip("-") == "name":  # a tree (#431): each parent, then its subgroups under it
        parent = aliased(Group)
        family = func.coalesce(select(func.lower(parent.name)).where(parent.id == Group.parent_id).scalar_subquery(),
                               func.lower(Group.name))
        order = [family.desc() if sort.startswith("-") else family.asc(), Group.parent_id.is_not(None),
                 func.lower(Group.name), Group.id]
    return paging.page_of(db, stmt, order, page, page_size, group_out)


@router.get("/{group_id}", response_model=GroupOut)
def group_overview(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return group_out(group_for_admin(group_id, p, db))


class SubgroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/{group_id}/subgroups", response_model=GroupOut, status_code=201)
def create_subgroup(group_id: uuid.UUID, body: SubgroupIn, request: Request, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    """A subgroup of this group (#431), by its admins (super admins and service admins too). It draws on the
    group's pages, and the group's admins manage it; its own email domains are a super admin's to add."""
    parent = subgroup_parent(db, group_for_admin(group_id, p, db).id)
    group = new_group(db, body.name, parent=parent)
    audit.record(db, request, "group.created", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"name": group.name, "parent": str(parent.id), "local_only": group.local_only,
                                             "require_approval": group.require_approval})
    db.commit()
    db.refresh(group)
    return group_out(group)


@router.get("/{group_id}/users", response_model=list[UserOut])
def group_users(group_id: uuid.UUID, q: str | None = Query(None, max_length=200),
                status: str | None = Query(None, pattern="^(active|disabled)$"),
                approval_state: str | None = Query(None, alias="approval", pattern="^(pending|approved|declined)$"),
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    stmt = select(User).where(User.group_id.in_(group_family(db, group.id)))  # its subgroups' people too (#431)
    if approval_state:
        stmt = stmt.where(User.approval == approval_state)
    if q:
        like = f"%{q.lower().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(User.email.like(like, escape="\\"), func.lower(User.name).like(like, escape="\\")))
    if status:
        stmt = stmt.where(User.status == status)
    return [user_out(u) for u in db.scalars(stmt.order_by(User.email).limit(limit).offset(offset))]


MEMBER_SORTS = {"email": User.email, "last_login": User.last_login_at, "joined": User.created_at}


@router.get("/{group_id}/people", response_model=Page[UserOut])
def group_people(group_id: uuid.UUID, q: str = Query("", max_length=200),
                 status: str | None = Query(None, pattern="^(active|disabled|declined|admin)$"),
                 service: str | None = Query(None, max_length=64), has: bool | None = None,
                 sort: str = Query("email", max_length=20), page: int = Query(1, ge=1, le=100_000),
                 page_size: int = Query(25, ge=1, le=200), p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    """A group's people for its admins, a page at a time (#514). People waiting for approval aren't here: the People
    tab lists them on their own (/users?approval=pending). Search by email or name; filter by status (active,
    disabled, declined, the group's admins) and, with `service`, by whether they have it; sort by email, last sign-in
    or when they joined."""
    group = group_for_admin(group_id, p, db)
    family = group_family(db, group.id)  # its subgroups' people too, each with their group (#431)
    stmt = select(User).where(User.group_id.in_(family), User.approval != "pending")
    if q.strip():
        pattern = paging.like(q)
        stmt = stmt.where(or_(User.email.like(pattern, escape="\\"), func.lower(User.name).like(pattern, escape="\\")))
    if status in ("active", "disabled"):
        stmt = stmt.where(User.status == status)
    elif status == "declined":
        stmt = stmt.where(User.approval == "declined")
    elif status == "admin":
        stmt = stmt.where(User.id.in_(select(RoleAssignment.user_id).where(
            RoleAssignment.role == "group_admin", RoleAssignment.group_id.in_(family))))
    if service and has is not None:
        gs = group.service(service)
        if gs is None:
            raise ApiError(422, "bad_filter", "Unknown service.")
        if not gs.all_users:  # (with everyone given it, everyone has it)
            granted = select(UserService.user_id).where(UserService.service_key == service, UserService.granted.is_(True))
            stmt = stmt.where(User.id.in_(granted) if has else User.id.not_in(granted))
        elif not has:
            stmt = stmt.where(false())
    return paging.page_of(db, stmt, paging.order_by(sort, MEMBER_SORTS, User.id), page, page_size, user_out)


@router.patch("/{group_id}/users/{user_id}", response_model=UserOut)
def update_member(group_id: uuid.UUID, user_id: uuid.UUID, body: MemberUpdate, request: Request,
                  p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    user = db.get(User, user_id)
    if user is None or user.group_id not in group_family(db, group.id):
        raise not_found("User")
    sent = body.model_dump(exclude_unset=True)
    if "status" in sent:
        if sent["status"] is None:
            raise ApiError(422, "invalid", "status cannot be empty.")
        if user.id == p.user.id:
            raise ApiError(422, "self_disable", "You can't change your own account status.")
        if user.across_groups and not p.is_super_admin:
            raise ApiError(403, "forbidden", "Only a super admin can disable a super admin or a service admin.")
    changes = {}
    if sent.get("group_id") is not None and sent["group_id"] != user.group_id:
        changes["group_id"] = _move(db, p, group, user, sent["group_id"])
    if "cap_pages" in sent:  # the standard service's cap
        us = services.user_service(db, user, services.STANDARD, create=True)
        if us.cap_pages != sent["cap_pages"]:
            changes["cap_pages"] = [us.cap_pages, sent["cap_pages"]]
            us.cap_pages = sent["cap_pages"]
    if "ai_describe" in sent and user.ai_describe != sent["ai_describe"]:
        changes["ai_describe"] = [user.ai_describe, sent["ai_describe"]]
        user.ai_describe = sent["ai_describe"]
    if "status" in sent and user.status != sent["status"]:
        changes["status"] = [user.status, sent["status"]]
        user.status = sent["status"]
    if changes.get("status", [None, None])[1] == "disabled":
        changes["sessions_revoked"] = revoke_sessions(db, [user.id], "user_disabled")
    if changes:
        action = "quota.cap_set" if set(changes) == {"cap_pages"} else "user.updated"
        audit.record(db, request, action, actor=p.user, target_type="user", target_id=user.id,
                     group_id=group.id, details={"email": user.email, **changes,
                                                 **by_whom(p, group.id)})
    db.commit()
    db.refresh(user)
    return user_out(user)


class GroupSettings(BaseModel):
    local_only: bool | None = None
    require_approval: bool | None = None  # new members wait for an admin's approval
    ai_mode: str | None = Field(None, pattern="^(choose|fixed)$")  # how documents get their AI
    ai_level: str | None = Field(None, max_length=64)  # fixed: the level ("" clears)
    ai_kind: str | None = Field(None, max_length=40)  # fixed: the kind ("" or "general": General)
    show_external: bool | None = None  # show people which options send documents to an external provider
    ai_describe: bool | None = None  # "Describe with AI" for its people; null follows the service default
    # Its people may make API tokens for scripts (#518), when super admins allow them; off stops existing tokens
    api_tokens_enabled: bool | None = None
    # Its people may choose PDF/UA-2 or Both (#445), when super admins offer it; and what's preselected
    pdfua_choice: bool | None = None
    pdfua_default: str | None = Field(None, pattern="^(1|2|both)$")
    # A subgroup's settings to take from its parent again (#431: ai, local_only, require_approval, ai_describe,
    # show_external, over_quota_message); one it sets above it overrides from then on
    inherit: list[str] | None = Field(None, max_length=10)


@router.patch("/{group_id}", response_model=GroupOut)
def update_group_settings(group_id: uuid.UUID, body: GroupSettings, request: Request,
                          p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Local-only keeps the group's documents away from every service that sends them to an external provider.
    Require approval makes new members wait for an admin before they can submit anything; people already
    waiting stay waiting until someone approves or declines them."""
    group = group_for_admin(group_id, p, db)
    sent = body.model_dump(exclude_unset=True)
    follow = sent.pop("inherit", None) or []
    if set(follow) & {inherit.SETTING_OF.get(k) for k in sent}:
        raise ApiError(422, "invalid", "A setting can't be set and taken from the parent at once.")
    if "ai_level" in sent:
        sent["ai_level"] = sent["ai_level"] or None
    if "ai_kind" in sent:
        sent["ai_kind"] = None if sent["ai_kind"] in (None, "", services.GENERAL) else sent["ai_kind"]
    mode = sent.get("ai_mode", group.ai_mode)
    level = sent.get("ai_level", group.ai_level)
    kind = sent.get("ai_kind", group.ai_kind if "ai_level" not in sent else None)
    if mode == "fixed":
        gs = group.service(level) if level else None
        if gs is None or not gs.enabled or gs.service.kind != "ai_tier":
            raise ApiError(422, "level_required", "Choose an AI level the group has, for everyone's documents.")
        if kind == services.AUTO:
            if not services.kinds(db, level) or services.classifier_for(db, group) is None:
                raise ApiError(422, "classifier_unavailable", "Letting AI decide the kind isn't available here.")
        elif kind and kind not in {k.key for k in services.kinds(db, level)}:
            raise ApiError(422, "unknown_kind", "That kind of document isn't offered with this AI level.")
        sent["ai_level"], sent["ai_kind"] = level, kind
    changed = set()
    for field, value in list(sent.items()):
        if value is None and field in ("local_only", "require_approval", "ai_mode", "show_external", "api_tokens_enabled",
                                       "pdfua_choice", "pdfua_default"):
            del sent[field]
            continue
        if field == "local_only":  # a subgroup can't loosen its parent's (#431)
            inherit.check_local_only(db, group, value)
        if value != getattr(group, field):
            audit.record(db, request, "group.updated", actor=p.user, target_type="group", target_id=group.id,
                         group_id=group.id, details={field: [getattr(group, field), value]})
            setattr(group, field, value)
            changed.add(field)
    inherit.overridden(group, set(sent))  # what a subgroup sets is its own from now on
    if follow:
        before = list(group.overrides or [])
        if back := inherit.follow(db, group, follow):
            audit.record(db, request, "group.updated", actor=p.user, target_type="group", target_id=group.id,
                         group_id=group.id, details={"follows_parent": back, "overrides": [before, group.overrides]})
    if subs := inherit.pass_down(db, group, changed):
        audit.record(db, request, "group.updated", actor=p.user, target_type="group", target_id=group.id,
                     group_id=group.id, details={"passed_to_subgroups": subs, "settings": sorted(changed)})
    db.commit()
    return group_out(group)


class Decline(BaseModel):
    reason: str | None = Field(None, max_length=1000)


def _move(db: Session, p: Principal, group: Group, user: User, to_id: uuid.UUID) -> list[str]:
    """Move a person between a group and its subgroups (#431). Whoever moves them administers both groups; their
    admin rights stay behind, and their past usage with the old group, as when a super admin moves someone."""
    top = group.parent_id or group.id
    to = db.get(Group, to_id)
    if to is None or to.id not in group_family(db, top):
        raise ApiError(422, "outside_family", "People can be moved only between a group and its subgroups. "
                                              "A super admin can move them to another group.")
    if not (p.can_admin_group(to.id) and p.can_admin_group(user.group_id)):
        raise ApiError(403, "forbidden", f"Only an administrator of both groups can move someone into {to.name}.")
    if user.id == p.user.id:
        raise ApiError(422, "self_move", "You can't move yourself; another administrator can.")
    if user.across_groups and not p.is_super_admin:
        raise ApiError(403, "forbidden", "Only a super admin can move a super admin or a service admin.")
    for role in [r for r in user.roles if r.role == "group_admin"]:
        db.delete(role)
    before = str(user.group_id)
    user.group_id = to.id
    return [before, str(to.id)]


def _member(db: Session, group: Group, user_id: uuid.UUID) -> User:
    """A person in the group or one of its subgroups (#431), for its admins."""
    user = db.get(User, user_id)
    if user is None or user.group_id not in group_family(db, group.id):
        raise not_found("User")
    return user


@router.post("/{group_id}/users/{user_id}/approve", response_model=UserOut)
def approve_member(group_id: uuid.UUID, user_id: uuid.UUID, request: Request,
                   p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Let someone who's waiting (or was declined) submit documents. They're told by email."""
    group = group_for_admin(group_id, p, db)
    user = _member(db, group, user_id)
    approval.approve(db, request, user, p.user)
    approval.notify_decision(db, user)
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.post("/{group_id}/users/{user_id}/decline", response_model=UserOut)
def decline_member(group_id: uuid.UUID, user_id: uuid.UUID, request: Request,
                   body: Decline | None = None, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    """Turn down someone who's waiting, with an optional reason they're shown. They can still be approved later."""
    group = group_for_admin(group_id, p, db)
    user = _member(db, group, user_id)
    reason = body.reason.strip() if body and body.reason else None
    approval.decline(db, request, user, p.user, reason)
    approval.notify_decision(db, user)
    db.commit()
    db.refresh(user)
    return user_out(user)


class GroupServiceUpdate(BaseModel):
    enabled: bool | None = None
    all_users: bool | None = None  # grant to every member instead of person by person
    default_user_cap: int | None = Field(None, ge=0, le=100_000_000)
    is_default: bool | None = None  # preselected at upload
    # A subgroup's limit on its parent's pool (#431; null: none), set by those who run the parent
    cap_pages: int | None = Field(None, ge=0, le=100_000_000)


@router.patch("/{group_id}/services/{key}", response_model=GroupOut)
def update_group_service(group_id: uuid.UUID, key: str, body: GroupServiceUpdate, request: Request,
                         p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    service = services.get_service(db, key)
    if service is None:
        raise not_found("Service")
    sent = body.model_dump(exclude_unset=True)
    for k in ("enabled", "all_users", "is_default"):
        if k in sent and sent[k] is None:
            raise ApiError(422, "invalid", f"{k} cannot be empty.")
    if sent.get("enabled"):
        if not service.enabled:
            raise ApiError(422, "service_disabled", f"{service.label} isn't switched on for this service yet.")
        if service.external and group.local_only:
            raise ApiError(422, "local_only", f"This group is local-only, so {service.label} (which sends documents "
                                              "to an external provider) can't be enabled. Turn local-only off first.")
    if key == services.STANDARD and sent.get("enabled") is False:
        raise ApiError(422, "standard_required", "The standard service can't be switched off for a group.")
    parent = group.pool if group.parent_id else None
    if sent.get("enabled") and parent is not None and not ((pgs := parent.service(key)) and pgs.enabled):
        raise ApiError(422, "parent_not_enabled", f"{parent.name} hasn't enabled {service.label}, so its subgroups "
                                                  "can't use it.")
    if "cap_pages" in sent:
        cap = sent.pop("cap_pages")
        if parent is None:
            raise ApiError(422, "not_subgroup", "Only a subgroup has a limit on its parent's pages.")
        if not p.can_admin_group(parent.id):  # not the subgroup's own admins: the pages are the parent's
            raise ApiError(403, "forbidden", f"Only {parent.name}'s administrators can change this limit.")
        before = (gs := services.group_service(db, group, key)) and gs.cap_pages
        if before != cap:
            quota.set_subgroup_cap(db, group, cap, quota.Actor(p.user.id, p.user.email, audit.client_ip(request)),
                                   service=key)
            audit.record(db, request, "group.service_updated", actor=p.user, target_type="group", target_id=group.id,
                         group_id=group.id, details={"service": key, "cap_pages": [before, cap]})
    gs = services.group_service(db, group, key, create=True)
    changes = {}
    for k, v in sent.items():
        if getattr(gs, k) != v:
            changes[k] = [getattr(gs, k), v]
            setattr(gs, k, v)
    if sent.get("is_default"):  # one default per kind (one processing option, one AI tier)
        for other in group.services:
            if other is not gs and other.is_default and other.service.kind == service.kind:
                other.is_default = False
    if changes:
        audit.record(db, request, "group.service_updated", actor=p.user, target_type="group", target_id=group.id,
                     group_id=group.id, details={"service": key, **changes})
    db.commit()
    db.refresh(group)
    return group_out(group)


class UserServiceUpdate(BaseModel):
    granted: bool | None = None
    cap_pages: int | None = Field(None, ge=0, le=10_000_000)  # null resets to the group default


@router.put("/{group_id}/users/{user_id}/services/{key}", response_model=UserOut)
def update_user_service(group_id: uuid.UUID, user_id: uuid.UUID, key: str, body: UserServiceUpdate,
                        request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    user = db.get(User, user_id)
    if user is None or user.group_id != group.id:
        raise not_found("User")
    if services.get_service(db, key) is None:
        raise not_found("Service")
    sent = body.model_dump(exclude_unset=True)
    if "granted" in sent and sent["granted"] is None:
        raise ApiError(422, "invalid", "granted cannot be empty.")
    us = services.user_service(db, user, key, create=True)
    changes = {}
    for k, v in sent.items():
        if getattr(us, k) != v:
            changes[k] = [getattr(us, k), v]
            setattr(us, k, v)
    if changes:
        action = "quota.cap_set" if set(changes) == {"cap_pages"} else "user.service_updated"
        audit.record(db, request, action, actor=p.user, target_type="user", target_id=user.id, group_id=group.id,
                     details={"email": user.email, "service": key, **changes,
                              **by_whom(p, group.id)})
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.get("/{group_id}/audit", response_model=list[AuditOut])
def group_audit(group_id: uuid.UUID, action: str | None = Query(None, max_length=64),
                actor_id: uuid.UUID | None = None, since: datetime | None = None, until: datetime | None = None,
                before_id: int | None = None, limit: int = Query(100, ge=1, le=1000),
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    stmt = audit_query(action=action, actor_id=actor_id, since=since, until=until, before_id=before_id)
    stmt = stmt.where(AuditEvent.group_id.in_(group_family(db, group.id)))
    return [AuditOut.model_validate(e, from_attributes=True) for e in db.scalars(stmt.limit(limit))]


@router.get("/{group_id}/attention")
def group_attention(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What needs this group's administrators' attention."""
    from portal.attention import Family, attention
    group = group_for_admin(group_id, p, db)
    return [i.to_dict() for i in attention(db, Family(group_family(db, group.id)))]


class AckIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)


@router.get("/{group_id}/attention/acknowledged")
def group_acknowledged(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The items this group's admins have acknowledged (#538)."""
    from portal.attention import Family, acknowledged
    return acknowledged(db, Family(group_family(db, group_for_admin(group_id, p, db).id)))


@router.post("/{group_id}/attention/acknowledge")
def group_acknowledge(group_id: uuid.UUID, body: AckIn, request: Request, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db)):
    """Acknowledge an item for every admin of the group, until something new joins it (#538)."""
    from portal.attention import Family, acknowledge, acked
    ids = Family(group_family(db, group_for_admin(group_id, p, db).id))
    return acked(db, request, p, ids, acknowledge(db, ids, body.key, p.user.email), body.key)


@router.delete("/{group_id}/attention/acknowledge")
def group_unacknowledge(group_id: uuid.UUID, request: Request, key: str = Query(min_length=1, max_length=200),
                        p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    from portal.attention import Family, unacked, unacknowledge
    ids = Family(group_family(db, group_for_admin(group_id, p, db).id))
    return unacked(db, request, p, ids, unacknowledge(db, ids, key), key)


@router.get("/{group_id}/access-check")
def group_access_check(group_id: uuid.UUID, email: str = Query(..., max_length=320),
                       service: str = Query(..., max_length=64), p: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    """Why a member of this group can or can't use a service."""
    from portal import services as svc
    from portal.attention import explain_access
    from portal.identity import lookup_email
    group = group_for_admin(group_id, p, db)
    user = db.scalar(select(User).where(User.email == lookup_email(email), User.group_id.in_(group_family(db, group.id))))
    if user is None:
        raise not_found("User")
    s = svc.get_service(db, service)
    if s is None:
        raise not_found("Service")
    return explain_access(db, user, s).to_dict()
