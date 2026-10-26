"""Which services a user can use, and on what terms.

A service is usable by a user when all of these hold:
  * it's switched on globally (super admin),
  * the group has it switched on (group admin), and the group isn't local-only when the service sends
    documents to an external provider; a subgroup's parent has it switched on too (#431),
  * the user is granted it: individually, or because the group grants it to all its members.
Quota (pages) is checked separately, when pages are reserved (portal.quota).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.models import AiKind, Group, GroupService, Service, User, UserService

STANDARD = "standard"


@dataclass
class Access:
    ok: bool
    code: str = ""
    message: str = ""


def get_service(db: Session, key: str) -> Service | None:
    return db.get(Service, key)


def all_services(db: Session) -> list[Service]:
    return list(db.scalars(select(Service).order_by(Service.sort, Service.label)))


def group_service(db: Session, group: Group, key: str, create: bool = False) -> GroupService | None:
    gs = group.service(key)
    if gs is None and create:
        gs = GroupService(group_id=group.id, service_key=key, enabled=False, all_users=False, pool_pages=0,
                          used_pages=0, reserved_pages=0, is_default=False)
        db.add(gs)
        group.services.append(gs)
    return gs


def user_service(db: Session, user: User, key: str, create: bool = False) -> UserService | None:
    us = user.service(key)
    if us is None and create:
        us = UserService(user_id=user.id, service_key=key, granted=False, used_pages=0, reserved_pages=0)
        db.add(us)
        user.services.append(us)
    return us


def setup_new_group(db: Session, group: Group) -> None:
    """A new group gets the standard service, for all its members; other services stay off until enabled."""
    gs = group_service(db, group, STANDARD, create=True)
    gs.enabled, gs.all_users, gs.is_default = True, True, True


def effective_cap(user: User, key: str = STANDARD) -> int | None:
    """The user's page cap for a service: their own, else the group default. None = no cap."""
    us = user.service(key)
    if us is not None and us.cap_pages is not None:
        return us.cap_pages
    gs = user.group.service(key) if user.group else None
    return gs.default_user_cap if gs else None


def granted(user: User, key: str) -> bool:
    gs = user.group.service(key) if user.group else None
    us = user.service(key)
    return bool(gs and (gs.all_users or (us is not None and us.granted)))


def access(db: Session, user: User, key: str) -> Access:
    service = get_service(db, key)
    if service is None:
        return Access(False, "unknown_service", "That processing option doesn't exist.")
    if not service.enabled:
        return Access(False, "service_disabled", f"{service.label} isn't available at the moment.")
    group = user.group
    if group is None:
        return Access(False, "no_group", "Your account isn't in a group.")
    if service.external and group.local_only:
        return Access(False, "local_only", f"Your group keeps documents on this service's own servers, so {service.label} "
                                           "can't be used.")
    gs = group.service(key)
    pool = group.pool.service(key)
    if gs is None or not gs.enabled or pool is None or not pool.enabled:
        return Access(False, "group_not_enabled", f"{service.label} isn't enabled for your group.")
    if not granted(user, key):
        return Access(False, "not_granted", f"You haven't been given access to {service.label}. "
                                            "Ask your group administrator.")
    return Access(True)


def ai_use(service: Service) -> str:
    """What an AI tier does for documents processed by this service: "full" (the standard route: alt text,
    tables, reading order) or "alt_text" (Adobe: alt text for Adobe's figures, in a separate copy, and
    optional per upload)."""
    return "alt_text" if service.engine_route == "adobe" else "full"


def full_review_allowed(db: Session, tier: str | None) -> bool:
    """'Review every figure' is allowed unless the AI tier doing the review has it switched off."""
    s = get_service(db, tier) if tier else None
    return s is None or s.allow_full_review


def subgroup_left(gs: GroupService) -> int | None:
    """What a subgroup may still use of its parent's pool under its own cap (#431). None: no cap."""
    return None if gs.cap_pages is None else gs.cap_pages - gs.used_pages - gs.reserved_pages


def pool_left(group: Group, key: str) -> tuple[int, int] | None:
    """(size, left) of a service's pages a group's documents may still use: its pool's, or for a subgroup its
    parent's within the subgroup's limit (the size is then the limit, if it has one). None: no row for it."""
    gs, pool = group.service(key), group.pool.service(key)
    if gs is None or pool is None:
        return None
    size, left = pool.pool_pages, pool.pool_pages - pool.used_pages - pool.reserved_pages
    if pool is not gs and gs.cap_pages is not None:
        size, left = gs.cap_pages, min(left, subgroup_left(gs))
    return size, left


def remaining(user: User, key: str, service: Service) -> int | None:
    """Pages the user can still submit to a service: the smallest of their cap, the group pool (a subgroup's
    parent's, and the subgroup's cap on it) and the global pool. None = unlimited (an unmetered service)."""
    if not service.metered:
        return None
    pl = pool_left(user.group, key) if user.group else None
    if pl is None:
        return 0
    left = pl[1]
    if service.pool_pages is not None:
        left = min(left, service.pool_pages - service.used_pages - service.reserved_pages)
    cap = effective_cap(user, key)
    if cap is not None:
        us = user.service(key)
        used = (us.used_pages + us.reserved_pages) if us else 0
        left = min(left, cap - used)
    return max(0, left)


# --- kinds of document within an AI level -------------------------------------------------------------

GENERAL = "general"
AUTO = "auto"  # "Let AI decide": the classifier picks the kind before the document is sent


def kinds(db: Session, level: str, enabled_only: bool = True) -> list:
    """The level's kinds of document beyond General, in order."""
    stmt = select(AiKind).where(AiKind.service_key == level).order_by(AiKind.sort, AiKind.label)
    if enabled_only:
        stmt = stmt.where(AiKind.enabled.is_(True))
    return list(db.scalars(stmt))


def kind_label(db: Session, level: str | None, kind: str | None) -> str:
    if not level or not kind:
        return "General"
    k = db.get(AiKind, (level, kind))
    return k.label if k else kind


def classifier_for(db: Session, group: Group | None) -> tuple[str, str | None] | None:
    """The classifier a group's documents may use: ("jev", None) — external, through OpenRouter — unless the group
    is local-only; else ("gateway", model) when a local model is set; None: no "Let AI decide"."""
    from portal import settings_store
    if group is None:
        return None
    rs = settings_store.load(db)
    if rs.classifier_jev and not group.local_only:
        return "jev", None
    if rs.classifier_local_model:
        return "gateway", rs.classifier_local_model
    return None

