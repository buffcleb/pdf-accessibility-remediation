"""Group-admin routes, scoped to groups the caller administers (super admins may use them for any group)."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from portal import approval, audit, services
from portal.db import get_db
from portal.deps import Principal, current_principal, group_for_admin
from portal.errors import ApiError, not_found
from portal.models import AuditEvent, Group, User
from portal.routers.admin import revoke_sessions
from portal.routers.common import AuditOut, audit_query
from portal.schemas import GroupOut, UserOut, group_out, user_out

router = APIRouter(prefix="/groups", tags=["group admin"])


class MemberUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(active|disabled)$")
    # Explicit page cap for the standard service; null resets the user to the group default
    cap_pages: int | None = Field(None, ge=0, le=10_000_000)
    # "Describe with AI" for this person; null follows the group
    ai_describe: bool | None = None


@router.get("", response_model=list[GroupOut])
def my_admin_groups(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Groups the caller administers (all groups for super admins)."""
    stmt = select(Group).order_by(Group.name)
    if not p.is_super_admin:
        ids = p.user.admin_group_ids()
        if not ids:
            return []
        stmt = stmt.where(Group.id.in_(ids))
    return [group_out(g) for g in db.scalars(stmt)]


@router.get("/{group_id}", response_model=GroupOut)
def group_overview(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return group_out(group_for_admin(group_id, p, db))


@router.get("/{group_id}/users", response_model=list[UserOut])
def group_users(group_id: uuid.UUID, q: str | None = Query(None, max_length=200),
                status: str | None = Query(None, pattern="^(active|disabled)$"),
                approval_state: str | None = Query(None, alias="approval", pattern="^(pending|approved|declined)$"),
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    stmt = select(User).where(User.group_id == group.id)
    if approval_state:
        stmt = stmt.where(User.approval == approval_state)
    if q:
        like = f"%{q.lower().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(User.email.like(like, escape="\\"), func.lower(User.name).like(like, escape="\\")))
    if status:
        stmt = stmt.where(User.status == status)
    return [user_out(u) for u in db.scalars(stmt.order_by(User.email).limit(limit).offset(offset))]


@router.patch("/{group_id}/users/{user_id}", response_model=UserOut)
def update_member(group_id: uuid.UUID, user_id: uuid.UUID, body: MemberUpdate, request: Request,
                  p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    user = db.get(User, user_id)
    if user is None or user.group_id != group.id:
        raise not_found("User")
    sent = body.model_dump(exclude_unset=True)
    if "status" in sent:
        if sent["status"] is None:
            raise ApiError(422, "invalid", "status cannot be empty.")
        if user.id == p.user.id:
            raise ApiError(422, "self_disable", "You can't change your own account status.")
        if user.is_super_admin and not p.is_super_admin:
            raise ApiError(403, "forbidden", "Only a super admin can disable a super admin.")
    changes = {}
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
                                                 "by_super_admin": p.is_super_admin and group.id not in p.user.admin_group_ids()})
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


@router.patch("/{group_id}", response_model=GroupOut)
def update_group_settings(group_id: uuid.UUID, body: GroupSettings, request: Request,
                          p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Local-only keeps the group's documents away from every service that sends them to an external provider.
    Require approval makes new members wait for an admin before they can submit anything; people already
    waiting stay waiting until someone approves or declines them."""
    group = group_for_admin(group_id, p, db)
    sent = body.model_dump(exclude_unset=True)
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
    for field, value in sent.items():
        if value is None and field in ("local_only", "require_approval", "ai_mode", "show_external"):
            continue
        if value != getattr(group, field):
            audit.record(db, request, "group.updated", actor=p.user, target_type="group", target_id=group.id,
                         group_id=group.id, details={field: [getattr(group, field), value]})
            setattr(group, field, value)
    db.commit()
    return group_out(group)


class Decline(BaseModel):
    reason: str | None = Field(None, max_length=1000)


def _member(db: Session, group: Group, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.group_id != group.id:
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
                              "by_super_admin": p.is_super_admin and group.id not in p.user.admin_group_ids()})
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
    stmt = stmt.where(AuditEvent.group_id == group.id)
    return [AuditOut.model_validate(e, from_attributes=True) for e in db.scalars(stmt.limit(limit))]


@router.get("/{group_id}/attention")
def group_attention(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What needs this group's administrators' attention."""
    from portal.attention import attention
    group = group_for_admin(group_id, p, db)
    return [i.to_dict() for i in attention(db, [group.id])]


@router.get("/{group_id}/access-check")
def group_access_check(group_id: uuid.UUID, email: str = Query(..., max_length=320),
                       service: str = Query(..., max_length=64), p: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)):
    """Why a member of this group can or can't use a service."""
    from portal import services as svc
    from portal.attention import explain_access
    from portal.identity import lookup_email
    group = group_for_admin(group_id, p, db)
    user = db.scalar(select(User).where(User.email == lookup_email(email), User.group_id == group.id))
    if user is None:
        raise not_found("User")
    s = svc.get_service(db, service)
    if s is None:
        raise not_found("Service")
    return explain_access(db, user, s).to_dict()
