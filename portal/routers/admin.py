"""Super-admin routes: groups, domains, group admins, users across groups, settings, audit log."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import approval, audit, quota, services, settings_store
from portal.db import get_db
from portal.deps import Principal, require_super_admin
from portal.errors import ApiError, conflict, not_found
from portal.identity import Eligibility, domain_of, lookup_email, normalize_domain, normalize_email, provision
from portal.models import AuditEvent, AuthSession, Group, GroupDomain, GroupEmail, RoleAssignment, Service, User
from portal.routers.common import AuditOut, LedgerOut, audit_query
from portal.schemas import GroupOut, UserOut, group_out, user_out
from portal.security.ratelimit import per_person

router = APIRouter(prefix="/admin", tags=["super admin"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def revoke_sessions(db: Session, user_ids, reason: str) -> int:
    return db.execute(update(AuthSession).where(AuthSession.user_id.in_(user_ids), AuthSession.revoked_at.is_(None))
                      .values(revoked_at=_now(), revoked_reason=reason)).rowcount


# --- groups --------------------------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=50)
    default_user_cap: int | None = Field(None, ge=0)  # standard service
    over_quota_message: str | None = Field(None, max_length=2000)
    local_only: bool = False
    require_approval: bool | None = None  # None: the default for new groups (a setting)


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    status: str | None = Field(None, pattern="^(active|disabled)$")
    default_user_cap: int | None = Field(None, ge=0)  # standard service
    over_quota_message: str | None = Field(None, max_length=2000)
    local_only: bool | None = None


class DomainIn(BaseModel):
    domain: str = Field(max_length=253)
    public_ok: bool = False  # yes, really allow everyone at a public email provider


# Public email providers: allowing one of these lets anyone with a free account in. Listing a person's address
# (below) is almost always what's meant.
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "gmx.net",
    "mail.com", "zoho.com", "yandex.com", "yandex.ru", "fastmail.com", "hey.com", "tutanota.com", "qq.com",
    "163.com", "126.com", "comcast.net", "verizon.net", "att.net", "sbcglobal.net",
}


def _claim_domain(db: Session, group: Group, raw: str, public_ok: bool = False) -> str:
    domain = normalize_domain(raw)
    if domain in PUBLIC_EMAIL_DOMAINS and not public_ok:
        raise conflict("public_domain", f"{domain} is a public email provider: anyone with a free {domain} address could "
                                        "sign in and join this group. To let one person in, add their address under "
                                        "People allowed by address instead.")
    existing = db.get(GroupDomain, domain)
    if existing is not None:
        if existing.group_id == group.id:
            return domain
        raise conflict("domain_taken", f"{domain} already belongs to the group \"{existing.group.name}\".")
    db.add(GroupDomain(domain=domain, group_id=group.id))
    return domain


@router.get("/groups", response_model=list[GroupOut])
def list_groups(_: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    return [group_out(g) for g in db.scalars(select(Group).order_by(Group.name))]


@router.post("/groups", response_model=GroupOut, status_code=201)
def create_group(body: GroupCreate, request: Request, p: Principal = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Group).where(func.lower(Group.name) == name.lower())):
        raise conflict("group_exists", f"A group named \"{name}\" already exists.")
    require = (body.require_approval if body.require_approval is not None
               else settings_store.load(db).new_groups_require_approval)
    group = Group(name=name, over_quota_message=body.over_quota_message, local_only=body.local_only,
                  require_approval=require)
    db.add(group)
    db.flush()
    services.setup_new_group(db, group)
    group.service(services.STANDARD).default_user_cap = body.default_user_cap
    domains = [_claim_domain(db, group, d) for d in body.domains]
    audit.record(db, request, "group.created", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"name": name, "domains": domains,
                                             "default_user_cap": body.default_user_cap,
                                             "local_only": body.local_only, "require_approval": require})
    db.commit()
    db.refresh(group)
    return group_out(group)


@router.get("/groups/{group_id}", response_model=GroupOut)
def get_group(group_id: uuid.UUID, _: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    return group_out(group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
def update_group(group_id: uuid.UUID, body: GroupUpdate, request: Request,
                 p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    changes = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if key in ("name", "status", "local_only") and value is None:
            raise ApiError(422, "invalid", f"{key} cannot be empty.")
        if key == "default_user_cap":
            gs = services.group_service(db, group, services.STANDARD, create=True)
            if gs.default_user_cap != value:
                changes[key] = [gs.default_user_cap, value]
                gs.default_user_cap = value
            continue
        if key == "name":
            value = value.strip()
            clash = db.scalar(select(Group).where(func.lower(Group.name) == value.lower(), Group.id != group.id))
            if clash:
                raise conflict("group_exists", f"A group named \"{value}\" already exists.")
        old = getattr(group, key)
        if old != value:
            setattr(group, key, value)
            changes[key] = [old, value]
    if changes.get("status", [None, None])[1] == "disabled":
        member_ids = select(User.id).where(User.group_id == group.id)
        changes["sessions_revoked"] = revoke_sessions(db, member_ids, "group_disabled")
    if changes:
        audit.record(db, request, "group.updated", actor=p.user, target_type="group", target_id=group.id,
                     group_id=group.id, details=changes)
    db.commit()
    return group_out(group)


@router.post("/groups/{group_id}/domains", response_model=GroupOut, status_code=201)
def add_domain(group_id: uuid.UUID, body: DomainIn, request: Request,
               p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    domain = _claim_domain(db, group, body.domain, body.public_ok)
    audit.record(db, request, "group.domain_added", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"domain": domain})
    try:
        db.commit()
    except IntegrityError:  # another request claimed it at the same moment
        db.rollback()
        raise conflict("domain_taken", f"{domain} already belongs to another group.") from None
    db.refresh(group)
    return group_out(group)


@router.delete("/groups/{group_id}/domains/{domain}", response_model=GroupOut)
def remove_domain(group_id: uuid.UUID, domain: str, request: Request,
                  p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """New accounts can no longer be created for this domain. Existing accounts keep working."""
    group = db.get(Group, group_id)
    row = db.get(GroupDomain, normalize_domain(domain))
    if group is None or row is None or row.group_id != group.id:
        raise not_found("Domain")
    db.delete(row)
    audit.record(db, request, "group.domain_removed", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"domain": row.domain})
    db.commit()
    db.refresh(group)
    return group_out(group)


# --- people allowed by address --------------------------------------------------------------------

class GroupEmailIn(BaseModel):
    email: str = Field(max_length=320)
    note: str | None = Field(None, max_length=300)
    move: bool = False  # it's listed on another group: move it here


@router.post("/groups/{group_id}/emails", response_model=GroupOut, status_code=201)
def add_group_email(group_id: uuid.UUID, body: GroupEmailIn, request: Request,
                    p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Let one person into the group by their address, without their domain. It takes precedence over a domain
    match. Someone who already has an account keeps theirs (and its group): move them in People instead."""
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    email = normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.group_id != group.id:
        where = user.group.name if user.group else "no group"
        raise conflict("has_account", f"{email} already has an account (in {where}). Accounts keep their group: "
                                      "to move them, use People (all groups) → Manage → Move.")
    existing = db.get(GroupEmail, email)
    if existing is not None and existing.group_id != group.id:
        if not body.move:
            raise conflict("email_taken", f"{email} is already allowed by address on the group "
                                          f"\"{existing.group.name}\". Move it to this group?")
        old = existing.group
        existing.group_id, existing.note, existing.added_by = group.id, body.note or existing.note, p.user.email
        audit.record(db, request, "group.email_removed", actor=p.user, target_type="group", target_id=old.id,
                     group_id=old.id, details={"email": email, "moved_to": group.name})
    elif existing is None:
        db.add(GroupEmail(email=email, group_id=group.id, note=(body.note or "").strip() or None,
                          added_by=p.user.email))
    else:
        existing.note = (body.note or "").strip() or existing.note
    audit.record(db, request, "group.email_added", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"email": email, **({"note": body.note} if body.note else {})})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("email_taken", f"{email} was just added to another group.") from None
    db.refresh(group)
    return group_out(group)


@router.delete("/groups/{group_id}/emails/{email}", response_model=GroupOut)
def remove_group_email(group_id: uuid.UUID, email: str, request: Request,
                       p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """They can no longer sign up by this address. An account they already have keeps working: disable it in
    People to stop that."""
    group = db.get(Group, group_id)
    row = db.get(GroupEmail, lookup_email(email))
    if group is None or row is None or row.group_id != group.id:
        raise not_found("Address")
    db.delete(row)
    audit.record(db, request, "group.email_removed", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"email": row.email})
    db.commit()
    db.refresh(group)
    return group_out(group)


# --- group admins --------------------------------------------------------------------------------

class AdminIn(BaseModel):
    email: str = Field(max_length=320)


@router.get("/groups/{group_id}/admins", response_model=list[UserOut])
def list_group_admins(group_id: uuid.UUID, _: Principal = Depends(require_super_admin),
                      db: Session = Depends(get_db)):
    if db.get(Group, group_id) is None:
        raise not_found("Group")
    users = db.scalars(select(User).join(RoleAssignment).where(
        RoleAssignment.role == "group_admin", RoleAssignment.group_id == group_id).order_by(User.email))
    return [user_out(u) for u in users]


@router.post("/groups/{group_id}/admins", response_model=UserOut, status_code=201)
def add_group_admin(group_id: uuid.UUID, body: AdminIn, request: Request,
                    p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Make a member of the group its admin. If they haven't signed in yet, their account is created
    now, provided their email domain belongs to this group or the group lists their address."""
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("Group")
    email = normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        if not any(d.domain == domain_of(email) for d in group.domains) and \
                not any(e.email == email for e in group.emails):
            raise ApiError(422, "domain_mismatch",
                           f"{email} isn't in one of this group's email domains or allowed by address, so it can't "
                           "join the group.")
        user = provision(db, email, "email", Eligibility(None, group, False))
        audit.record(db, request, "user.provisioned", actor=p.user, target_type="user", target_id=user.id,
                     group_id=group.id, details={"method": "admin", "email": email})
    elif user.group_id != group.id:
        raise conflict("other_group", f"{email} belongs to another group. Move the user first.")
    approval.mark_approved_as_admin(user, p.user.email)
    if group.id not in user.admin_group_ids():
        db.add(RoleAssignment(user_id=user.id, role="group_admin", group_id=group.id))
        audit.record(db, request, "role.granted", actor=p.user, target_type="user", target_id=user.id,
                     group_id=group.id, details={"role": "group_admin", "email": email})
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.delete("/groups/{group_id}/admins/{user_id}", status_code=204)
def remove_group_admin(group_id: uuid.UUID, user_id: uuid.UUID, request: Request,
                       p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    role = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user_id,
                                                  RoleAssignment.role == "group_admin",
                                                  RoleAssignment.group_id == group_id))
    if role is None:
        raise not_found("Group admin")
    email = role.user.email
    db.delete(role)
    audit.record(db, request, "role.revoked", actor=p.user, target_type="user", target_id=user_id,
                 group_id=group_id, details={"role": "group_admin", "email": email})
    db.commit()


# --- users across groups -------------------------------------------------------------------------

class UserAdminUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    status: str | None = Field(None, pattern="^(active|disabled)$")
    group_id: uuid.UUID | None = None


@router.get("/users", response_model=list[UserOut])
def search_users(q: str | None = Query(None, max_length=200), group_id: uuid.UUID | None = None,
                 status: str | None = Query(None, pattern="^(active|disabled)$"),
                 limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                 _: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        like = f"%{q.lower().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(User.email.like(like, escape="\\"), func.lower(User.name).like(like, escape="\\")))
    if group_id:
        stmt = stmt.where(User.group_id == group_id)
    if status:
        stmt = stmt.where(User.status == status)
    return [user_out(u) for u in db.scalars(stmt.order_by(User.email).limit(limit).offset(offset))]


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserAdminUpdate, request: Request,
                p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    sent = body.model_dump(exclude_unset=True)
    changes = {}
    if "status" in sent and sent["status"] is None:
        raise ApiError(422, "invalid", "status cannot be empty.")
    if sent.get("status") == "disabled" and user.id == p.user.id:
        raise ApiError(422, "self_disable", "You can't disable your own account.")
    if "group_id" in sent and sent["group_id"] != user.group_id:
        new_group = db.get(Group, sent["group_id"]) if sent["group_id"] else None
        if sent["group_id"] and new_group is None:
            raise not_found("Group")
        if new_group is None and not user.is_super_admin:
            raise ApiError(422, "group_required", "Only super admins can be without a group.")
        # Admin rights don't follow the user to a new group. Past usage stays with the old group.
        for role in [r for r in user.roles if r.role == "group_admin"]:
            db.delete(role)
        changes["group_id"] = [str(user.group_id) if user.group_id else None,
                               str(new_group.id) if new_group else None]
        user.group_id = new_group.id if new_group else None
    for key in ("name", "status"):
        if key in sent and getattr(user, key) != sent[key]:
            changes[key] = [getattr(user, key), sent[key]]
            setattr(user, key, sent[key])
    if changes.get("status", [None, None])[1] == "disabled":
        changes["sessions_revoked"] = revoke_sessions(db, [user.id], "user_disabled")
    if changes:
        audit.record(db, request, "user.updated", actor=p.user, target_type="user", target_id=user.id,
                     group_id=user.group_id, details={"email": user.email, **changes})
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.post("/users/{user_id}/super-admin", response_model=UserOut)
def grant_super_admin(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_super_admin),
                      db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    if not user.is_super_admin:
        approval.mark_approved_as_admin(user, p.user.email)
        db.add(RoleAssignment(user_id=user.id, role="super_admin"))
        audit.record(db, request, "role.granted", actor=p.user, target_type="user", target_id=user.id,
                     group_id=user.group_id, details={"role": "super_admin", "email": user.email})
        db.commit()
        db.refresh(user)
    return user_out(user)


@router.post("/users/{user_id}/sessions/end", status_code=204)
def end_sessions(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    """Sign a person out everywhere (#354): a lost device, or an account someone else may be using."""
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    count = revoke_sessions(db, [user.id], "ended_by_admin")
    audit.record(db, request, "sessions.ended", actor=p.user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"sessions": count})
    db.commit()


# --- a person's data (#338) ---------------------------------------------------------------------------

class RemovePerson(BaseModel):
    confirm_email: str = Field(..., max_length=320)  # typed by the super admin: removing is final
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/users/{user_id}/remove", response_model=UserOut)
def remove_person(user_id: uuid.UUID, body: RemovePerson, request: Request, p: Principal = Depends(require_super_admin),
                  db: Session = Depends(get_db)):
    """Remove a person's data on their request: their files deleted, their name and email gone from every record,
    the records themselves kept. Final."""
    from portal import privacy

    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise not_found("User")
    if body.confirm_email.strip().lower() != user.email.lower():
        raise ApiError(422, "confirm_email", "Type the person's email address to confirm.")
    counts = privacy.remove_person(db, request, user, p.user)
    audit.record(db, request, "person.removal_reason", actor=p.user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"reason": body.reason.strip(), **counts})
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.get("/users/{user_id}/export", dependencies=[Depends(per_person("export"))])
def export_person(user_id: uuid.UUID, request: Request, files: bool = False,
                  p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Everything held about a person, for a records request: JSON, or with files=true a zip that also has their
    documents' files that still exist. Audited."""
    from fastapi.responses import Response

    from portal import privacy

    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    content, media, name = privacy.export_person(db, user, files)
    audit.record(db, request, "person.exported", actor=p.user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"files": files, "bytes": len(content)})
    db.commit()
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                        "Cache-Control": "no-store"})


@router.delete("/users/{user_id}/super-admin", response_model=UserOut)
def revoke_super_admin(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_super_admin),
                       db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None or not user.is_super_admin:
        raise not_found("Super admin")
    # Lock all super-admin rows so two admins can't remove each other at the same moment.
    supers = db.scalars(select(RoleAssignment).where(RoleAssignment.role == "super_admin").with_for_update()).all()
    active = [r for r in supers if r.user.status == "active"]
    if len(active) <= 1 and any(r.user_id == user.id for r in active):
        raise conflict("last_super_admin", "You can't remove the last active super admin.")
    if user.group_id is None:
        raise ApiError(422, "group_required", "Assign this user to a group before removing super admin rights.")
    for r in supers:
        if r.user_id == user.id:
            db.delete(r)
    audit.record(db, request, "role.revoked", actor=p.user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"role": "super_admin", "email": user.email})
    db.commit()
    db.refresh(user)
    return user_out(user)


# --- settings ------------------------------------------------------------------------------------

@router.get("/settings", response_model=settings_store.RuntimeSettings)
def get_settings(_: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    return settings_store.load(db)


@router.patch("/settings", response_model=settings_store.RuntimeSettings)
def patch_settings(body: settings_store.RuntimeSettingsUpdate, request: Request,
                   p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    new, diff = settings_store.update(db, body, p.user.id)
    if diff:
        audit.record(db, request, "settings.updated", actor=p.user, target_type="settings", details=diff)
    db.commit()
    return new


# --- audit log -----------------------------------------------------------------------------------

@router.get("/audit", response_model=list[AuditOut])
def all_audit(action: str | None = Query(None, max_length=64), group_id: uuid.UUID | None = None,
              actor_id: uuid.UUID | None = None, since: datetime | None = None, until: datetime | None = None,
              before_id: int | None = None, limit: int = Query(100, ge=1, le=1000),
              _: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    stmt = audit_query(action=action, actor_id=actor_id, since=since, until=until, before_id=before_id)
    if group_id:
        stmt = stmt.where(AuditEvent.group_id == group_id)
    return [AuditOut.model_validate(e, from_attributes=True) for e in db.scalars(stmt.limit(limit))]


# --- quota: top-ups and adjustments --------------------------------------------------------------

class TopupIn(BaseModel):
    pages: int = Field(gt=0, le=100_000_000)
    ticket_ref: str | None = Field(None, max_length=100)
    ticket_url: str | None = Field(None, max_length=500, pattern=r"^https?://")
    override: bool = False
    comment: str | None = Field(None, max_length=2000)
    service: str = Field(services.STANDARD, max_length=64)


class AdjustIn(BaseModel):
    pages: int = Field(ge=-100_000_000, le=100_000_000)
    comment: str = Field(min_length=1, max_length=2000)
    ticket_ref: str | None = Field(None, max_length=100)
    service: str = Field(services.STANDARD, max_length=64)


def _actor(p: Principal, request: Request) -> quota.Actor:
    return quota.Actor(p.user.id, p.user.email, audit.client_ip(request))


def _record_topup(db, request, p, entry, body, group_id) -> None:
    audit.record(db, request, "quota.topup", actor=p.user, target_type="group" if group_id else "service",
                 target_id=group_id, group_id=group_id, outcome="override" if entry.override else "success",
                 details={"service": body.service, "scope": entry.scope, "pages": body.pages,
                          "ticket_ref": entry.ticket_ref, "override": entry.override, "comment": entry.comment,
                          "pool_after": entry.pool_after})


def _record_adjust(db, request, p, entry, body, group_id) -> None:
    audit.record(db, request, "quota.adjusted", actor=p.user, target_type="group" if group_id else "service",
                 target_id=group_id, group_id=group_id,
                 details={"service": body.service, "scope": entry.scope, "pages": body.pages,
                          "comment": entry.comment, "pool_after": entry.pool_after})


@router.post("/groups/{group_id}/topups", response_model=LedgerOut, status_code=201)
def add_topup(group_id: uuid.UUID, body: TopupIn, request: Request, p: Principal = Depends(require_super_admin),
              db: Session = Depends(get_db)):
    entry = quota.topup(db, group_id, body.pages, _actor(p, request), ticket_ref=body.ticket_ref,
                        ticket_url=body.ticket_url, override=body.override, comment=body.comment, service=body.service)
    _record_topup(db, request, p, entry, body, group_id)
    db.commit()
    return LedgerOut.model_validate(entry, from_attributes=True)


@router.post("/groups/{group_id}/adjustments", response_model=LedgerOut, status_code=201)
def add_adjustment(group_id: uuid.UUID, body: AdjustIn, request: Request,
                   p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    entry = quota.adjust(db, group_id, body.pages, _actor(p, request), comment=body.comment,
                         ticket_ref=body.ticket_ref, service=body.service)
    _record_adjust(db, request, p, entry, body, group_id)
    db.commit()
    return LedgerOut.model_validate(entry, from_attributes=True)


class UsageCorrectionIn(BaseModel):
    service: str = Field(services.STANDARD, max_length=64)
    pages: int = Field(ge=-10_000_000, le=10_000_000)  # negative gives pages back, positive charges more
    comment: str = Field(min_length=1, max_length=2000)
    ticket_ref: str | None = Field(None, max_length=100)


@router.post("/users/{user_id}/usage-corrections", response_model=UserOut, status_code=201)
def correct_user_usage(user_id: uuid.UUID, body: UsageCorrectionIn, request: Request,
                       p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Correct one person's usage of a service. Recorded in the ledger and in both the super admin's and
    the group's audit log."""
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User")
    entry, before, after = quota.correct_usage(db, user, body.pages, _actor(p, request), comment=body.comment,
                                               ticket_ref=body.ticket_ref, service=body.service)
    audit.record(db, request, "quota.usage_corrected", actor=p.user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"email": user.email, "service": body.service, "pages": body.pages,
                                                  "used_pages": [before, after], "comment": entry.comment,
                                                  "ticket_ref": entry.ticket_ref, "by_super_admin": True})
    db.commit()
    db.refresh(user)
    return user_out(user)


# --- services ------------------------------------------------------------------------------------

class KindOut(BaseModel):
    key: str
    label: str
    description: str | None
    gateway_model: str
    gateway_judge_model: str | None
    enabled: bool
    sort: int


class KindCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    gateway_model: str = Field(min_length=1, max_length=200)
    gateway_judge_model: str | None = Field(None, max_length=200)  # empty: the level's


class KindUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    gateway_model: str | None = Field(None, min_length=1, max_length=200)
    gateway_judge_model: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    sort: int | None = Field(None, ge=0, le=10_000)


class ServiceOut(BaseModel):
    key: str
    kind: str
    label: str
    description: str | None
    enabled: bool
    external: bool
    notice_enabled: bool
    notice_text: str | None
    metered: bool
    global_limited: bool
    pool_pages: int | None
    used_pages: int
    reserved_pages: int
    remaining_pages: int | None
    daily_cap_pages: int | None
    max_at_once: int | None
    pages_sent_today: int
    max_pages_per_job: int | None
    confirm_before_send: bool
    gateway_model: str | None
    gateway_judge_model: str | None
    allow_full_review: bool
    allow_notation: bool
    allow_ocr_ai: bool
    engine_route: str
    audit_ai: str  # AI levels: audits "free" (use AI, no pages), "charge" (this level's pages) or "off" (no AI)
    sort: int
    groups_enabled: int
    kinds: list[KindOut] = []  # AI levels: kinds of document beyond General, with their models (super admins only)


class ServiceUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=200)
    gateway_model: str | None = Field(None, min_length=1, max_length=200)  # AI tiers only
    gateway_judge_model: str | None = Field(None, max_length=200)  # AI tiers only; empty = engine default
    allow_full_review: bool | None = None  # AI tiers: people may ask for every figure to be reviewed
    allow_notation: bool | None = None  # AI tiers: the AI reads specialist notation the layout rules can't
    allow_ocr_ai: bool | None = None  # AI tiers: the AI corrects OCR words Tesseract was unsure of
    audit_ai: str | None = Field(None, pattern="^(free|charge|off)$")  # AI levels: AI in audits
    description: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    external: bool | None = None
    notice_enabled: bool | None = None
    notice_text: str | None = Field(None, max_length=4000)
    metered: bool | None = None
    global_limited: bool | None = None
    daily_cap_pages: int | None = Field(None, ge=0, le=100_000_000)
    max_at_once: int | None = Field(None, ge=1, le=10_000)
    max_pages_per_job: int | None = Field(None, ge=1, le=1_000_000)
    confirm_before_send: bool | None = None
    sort: int | None = Field(None, ge=0, le=10_000)


def service_out(db: Session, s: Service) -> ServiceOut:
    from portal.models import GroupService
    groups = db.scalar(select(func.count()).select_from(GroupService).where(
        GroupService.service_key == s.key, GroupService.enabled)) or 0
    return ServiceOut(
        key=s.key, kind=s.kind, label=s.label, description=s.description, enabled=s.enabled, external=s.external,
        notice_enabled=s.notice_enabled, notice_text=s.notice_text, metered=s.metered,
        global_limited=s.pool_pages is not None, pool_pages=s.pool_pages, used_pages=s.used_pages,
        reserved_pages=s.reserved_pages,
        remaining_pages=None if s.pool_pages is None else max(0, s.pool_pages - s.used_pages - s.reserved_pages),
        daily_cap_pages=s.daily_cap_pages, max_at_once=s.max_at_once, pages_sent_today=quota.pages_sent_today(db, s.key),
        max_pages_per_job=s.max_pages_per_job, confirm_before_send=s.confirm_before_send,
        gateway_model=s.gateway_model, gateway_judge_model=s.gateway_judge_model,
        allow_full_review=s.allow_full_review, allow_notation=s.allow_notation, allow_ocr_ai=s.allow_ocr_ai,
        engine_route=s.engine_route, audit_ai=s.audit_ai,
        sort=s.sort,
        groups_enabled=groups,
        kinds=[_kind_out(k) for k in services.kinds(db, s.key, enabled_only=False)] if s.kind == "ai_tier" else [])


def _kind_out(k) -> KindOut:
    return KindOut(key=k.key, label=k.label, description=k.description, gateway_model=k.gateway_model,
                   gateway_judge_model=k.gateway_judge_model, enabled=k.enabled, sort=k.sort)


def _level(db: Session, key: str) -> Service:
    s = services.get_service(db, key)
    if s is None or s.kind != "ai_tier":
        raise not_found("AI level")
    return s


@router.post("/services/{key}/kinds", response_model=ServiceOut, status_code=201)
def add_kind(key: str, body: KindCreate, request: Request, p: Principal = Depends(require_super_admin),
             db: Session = Depends(get_db)):
    """A kind of document within an AI level (Math and science, Forms…), with the gateway models that suit it.
    People see the kind's name and description, never its models."""
    from portal.models import AiKind
    s = _level(db, key)
    slug = "-".join("".join(c if c.isalnum() else " " for c in body.label.lower()).split())[:40] or "kind"
    if slug in (services.GENERAL, services.AUTO) or db.get(AiKind, (key, slug)) is not None:
        raise conflict("kind_exists", f"{s.label} already has a kind called \"{body.label}\".")
    k = AiKind(service_key=key, key=slug, label=body.label.strip(), description=(body.description or "").strip() or None,
               gateway_model=body.gateway_model.strip(),
               gateway_judge_model=(body.gateway_judge_model or "").strip() or None, enabled=True,
               sort=len(services.kinds(db, key, enabled_only=False)) + 1)
    db.add(k)
    audit.record(db, request, "service.kind_added", actor=p.user, target_type="service", target_id=key,
                 details={"kind": slug, "label": k.label, "model": k.gateway_model, "judge": k.gateway_judge_model})
    db.commit()
    return service_out(db, s)


@router.patch("/services/{key}/kinds/{kind}", response_model=ServiceOut)
def update_kind(key: str, kind: str, body: KindUpdate, request: Request, p: Principal = Depends(require_super_admin),
                db: Session = Depends(get_db)):
    from portal.models import AiKind
    s = _level(db, key)
    k = db.get(AiKind, (key, kind))
    if k is None:
        raise not_found("Kind of document")
    sent = body.model_dump(exclude_unset=True)
    for f in ("label", "gateway_model", "enabled", "sort"):
        if f in sent and sent[f] is None:
            raise ApiError(422, "invalid", f"{f} cannot be empty.")
    for f in ("description", "gateway_judge_model"):
        if f in sent:
            sent[f] = (sent[f] or "").strip() or None
    changes = {f: [getattr(k, f), v] for f, v in sent.items() if getattr(k, f) != v}
    for f, v in sent.items():
        setattr(k, f, v.strip() if isinstance(v, str) else v)
    if changes:
        audit.record(db, request, "service.kind_changed", actor=p.user, target_type="service", target_id=key,
                     details={"kind": kind, **changes})
    db.commit()
    return service_out(db, s)


@router.delete("/services/{key}/kinds/{kind}", response_model=ServiceOut)
def remove_kind(key: str, kind: str, request: Request, p: Principal = Depends(require_super_admin),
                db: Session = Depends(get_db)):
    """Documents already waiting for it are processed with the level's General models."""
    from portal.models import AiKind
    s = _level(db, key)
    k = db.get(AiKind, (key, kind))
    if k is None:
        raise not_found("Kind of document")
    db.delete(k)
    audit.record(db, request, "service.kind_removed", actor=p.user, target_type="service", target_id=key,
                 details={"kind": kind, "label": k.label})
    db.commit()
    return service_out(db, s)


class AITierCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    gateway_model: str = Field(min_length=1, max_length=200)
    # The review model: pick one from a different model family than gateway_model
    gateway_judge_model: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)
    external: bool = False
    metered: bool = True


class AdminLinks(BaseModel):
    gateway_admin_url: str | None


@router.get("/links", response_model=AdminLinks)
def admin_links(_: Principal = Depends(require_super_admin)):
    """Other consoles super admins use, for links in Administration."""
    from portal.config import get_portal_settings
    return AdminLinks(gateway_admin_url=get_portal_settings().gateway_admin_url.strip() or None)


@router.get("/ai-models")
def ai_models(_: Principal = Depends(require_super_admin)):
    """Models the AI gateway serves, to publish as AI tiers."""
    from portal.engine import EngineUnavailable, get_engine_client
    try:
        return {"models": get_engine_client().ai_models()}
    except EngineUnavailable as e:
        raise ApiError(503, "gateway_unavailable", f"The AI gateway can't be reached: {e}") from None


@router.post("/services", response_model=ServiceOut, status_code=201)
def create_ai_tier(body: AITierCreate, request: Request, p: Principal = Depends(require_super_admin),
                   db: Session = Depends(get_db)):
    """Publish a gateway model as an AI tier. It starts switched off; groups enable it once it's on."""
    slug = "-".join("".join(c if c.isalnum() else " " for c in body.label.lower()).split())[:40] or "tier"
    key = f"ai:{slug}"
    if services.get_service(db, key) is not None:
        raise conflict("service_exists", f"An AI level called \"{body.label}\" already exists.")
    s = Service(key=key, kind="ai_tier", label=body.label.strip(), description=body.description,
                gateway_model=body.gateway_model.strip(),
                gateway_judge_model=(body.gateway_judge_model or "").strip() or None, enabled=False, external=body.external,
                notice_enabled=False, metered=body.metered, used_pages=0, reserved_pages=0, confirm_before_send=False,
                sort=100 + len(services.all_services(db)))
    db.add(s)
    audit.record(db, request, "service.created", actor=p.user, target_type="service",
                 details={"service": key, "label": s.label, "gateway_model": s.gateway_model,
                          "gateway_judge_model": s.gateway_judge_model, "external": s.external,
                          "metered": s.metered})
    db.commit()
    return service_out(db, s)


@router.get("/services", response_model=list[ServiceOut])
def list_services(_: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    return [service_out(db, s) for s in services.all_services(db)]


@router.patch("/services/{key}", response_model=ServiceOut)
def update_service(key: str, body: ServiceUpdate, request: Request, p: Principal = Depends(require_super_admin),
                   db: Session = Depends(get_db)):
    s = services.get_service(db, key)
    if s is None:
        raise not_found("Service")
    sent = body.model_dump(exclude_unset=True)
    for k in ("label", "enabled", "external", "notice_enabled", "metered", "global_limited", "confirm_before_send",
              "sort", "allow_full_review", "allow_notation", "allow_ocr_ai", "audit_ai"):
        if k in sent and sent[k] is None:
            raise ApiError(422, "invalid", f"{k} cannot be empty.")
    if key == services.STANDARD and sent.get("enabled") is False:
        raise ApiError(422, "standard_required", "The standard service can't be switched off.")
    if "gateway_judge_model" in sent:
        sent["gateway_judge_model"] = (sent["gateway_judge_model"] or "").strip() or None
    if ("gateway_model" in sent or "gateway_judge_model" in sent or "audit_ai" in sent) and s.kind != "ai_tier":
        raise ApiError(422, "not_ai_tier", "Only AI levels have a gateway model.")
    if s.engine_route == "adobe" and sent.get("external") is False:
        raise ApiError(422, "external_required", "Adobe Autotag sends documents to Adobe, so it stays marked external.")
    if key == services.STANDARD and sent.get("external"):
        raise ApiError(422, "standard_local", "The standard service runs on this service's own servers.")
    changes = {}
    if "global_limited" in sent:
        before = s.pool_pages is not None
        if before != sent["global_limited"]:
            quota.set_global_limit(db, key, sent["global_limited"])
            changes["global_limited"] = [before, sent["global_limited"]]
        del sent["global_limited"]
    for k, v in sent.items():
        if getattr(s, k) != v:
            changes[k] = [getattr(s, k), v]
            setattr(s, k, v)
    if changes:
        audit.record(db, request, "service.updated", actor=p.user, target_type="service", details={"service": key,
                                                                                                   **changes})
    db.commit()
    return service_out(db, s)


@router.delete("/services/{key}", status_code=204)
def delete_ai_tier(key: str, request: Request, p: Principal = Depends(require_super_admin),
                   db: Session = Depends(get_db)):
    """Remove an AI level for good: its kinds of document, and every group's and person's settings and page
    counts for it, go with it. It must be switched off and have no document waiting or in progress, so nothing
    loses its level half way. Documents already processed with it keep their record, the page ledger and the
    audit log keep their rows, and reports show the level by its key from then on."""
    from sqlalchemy import delete

    from portal import queues
    from portal.models import DocumentVersion, GroupService, Job, QueuePause
    s = db.scalar(select(Service).where(Service.key == key).with_for_update())
    if s is None:
        raise not_found("Service")
    if s.kind != "ai_tier":
        raise ApiError(422, "not_ai_tier", "Only AI levels can be deleted.")
    if s.enabled:
        raise conflict("service_on", "Switch this AI level off before deleting it.")
    documents = db.scalar(select(func.count()).select_from(Job).where(
        Job.ai_tier == key, Job.status.in_(("held", "queued", "submitted", "processing")))) or 0
    versions = db.scalar(select(func.count()).select_from(DocumentVersion).join(Job, Job.id == DocumentVersion.job_id).where(
        Job.ai_tier == key, or_(DocumentVersion.status.in_(("queued", "submitted", "processing")),
                                DocumentVersion.review_status.in_(("queued", "running"))))) or 0
    if documents or versions:
        raise conflict("service_in_use", f"{documents + versions} document(s) are waiting or in progress with this AI "
                                         "level. Delete it when they have finished.")
    groups = db.scalar(select(func.count()).select_from(GroupService).where(GroupService.service_key == key)) or 0
    audit.record(db, request, "service.deleted", actor=p.user, target_type="service",
                 details={"service": key, "label": s.label, "gateway_model": s.gateway_model,
                          "gateway_judge_model": s.gateway_judge_model, "external": s.external,
                          "used_pages": s.used_pages, "groups": groups})
    db.execute(delete(QueuePause).where(QueuePause.key == queues.service_key(key)))  # a pause of nothing
    db.delete(s)  # kinds, group and user rows follow by the database's cascade
    db.commit()


@router.post("/services/{key}/topups", response_model=LedgerOut, status_code=201)
def add_service_topup(key: str, body: TopupIn, request: Request, p: Principal = Depends(require_super_admin),
                      db: Session = Depends(get_db)):
    """Pages for a service's global pool."""
    body.service = key
    entry = quota.topup(db, None, body.pages, _actor(p, request), ticket_ref=body.ticket_ref,
                        ticket_url=body.ticket_url, override=body.override, comment=body.comment, service=key)
    _record_topup(db, request, p, entry, body, None)
    db.commit()
    return LedgerOut.model_validate(entry, from_attributes=True)


@router.post("/services/{key}/adjustments", response_model=LedgerOut, status_code=201)
def add_service_adjustment(key: str, body: AdjustIn, request: Request, p: Principal = Depends(require_super_admin),
                           db: Session = Depends(get_db)):
    body.service = key
    entry = quota.adjust(db, None, body.pages, _actor(p, request), comment=body.comment, ticket_ref=body.ticket_ref,
                         service=key)
    _record_adjust(db, request, p, entry, body, None)
    db.commit()
    return LedgerOut.model_validate(entry, from_attributes=True)


# --- attention and access checks ----------------------------------------------------------------------

@router.get("/attention")
def admin_attention(_: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """What needs a super admin's attention, across every group and service, most serious first."""
    from portal.attention import attention
    return [i.to_dict() for i in attention(db)]


@router.get("/access-check")
def admin_access_check(email: str = Query(..., max_length=320), service: str = Query(..., max_length=64),
                       _: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Why this person can or can't use this service: every check in order."""
    from portal.attention import explain_access
    user = db.scalar(select(User).where(User.email == lookup_email(email)))
    if user is None:
        raise not_found("User")
    s = services.get_service(db, service)
    if s is None:
        raise not_found("Service")
    return explain_access(db, user, s).to_dict()
