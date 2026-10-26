"""Page quota accounting, per service.

Every service has a page pool at three levels: global (optional), per group, and a cap per user. A job
reserves its pages on every service it uses (its processing service and, later, its AI tier) at all
three levels at once, or not at all. A subgroup has no pool of its own (#431): its documents use its
parent's, up to the subgroup's own cap when it has one, and its own rows count what it has used.

Every change goes through here and writes ledger entries in the same transaction. Rows are locked
(SELECT ... FOR UPDATE) in one fixed order, services by key, then the group's service rows (a parent's
before its subgroup's), then the user's, so parallel uploads and job completions can't overspend, lose
updates or deadlock.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from portal import services as svc
from portal import settings_store
from portal.errors import ApiError
from portal.models import (
    ACTIVE_JOB_STATUSES, Group, GroupService, Job, QuotaLedger, Service, User, UserService,
)


@dataclass
class Actor:
    id: uuid.UUID | None = None
    email: str | None = None
    ip: str | None = None


SYSTEM = Actor(email="system")


class OverQuota(ApiError):
    def __init__(self, message: str, scope: str, remaining: int, needed: int, support_url: str | None,
                 service: str = svc.STANDARD):
        super().__init__(403, "over_quota", message)
        self.detail.update({"scope": scope, "remaining_pages": remaining, "needed_pages": needed,
                            "support_url": support_url, "service": service})


def _lock_services(db: Session, keys: list[str]) -> dict[str, Service]:
    rows = db.scalars(select(Service).where(Service.key.in_(keys)).order_by(Service.key).with_for_update()
                      .execution_options(populate_existing=True)).all()
    return {s.key: s for s in rows}


def _lock_group_services(db: Session, group_id: uuid.UUID, keys: list[str]) -> dict[str, GroupService]:
    rows = db.scalars(select(GroupService).where(GroupService.group_id == group_id, GroupService.service_key.in_(keys))
                      .order_by(GroupService.service_key).with_for_update()
                      .execution_options(populate_existing=True)).all()
    return {r.service_key: r for r in rows}


def _lock_pools(db: Session, group: Group, keys: list[str]
                ) -> tuple[dict[str, GroupService], dict[str, GroupService]]:
    """The rows of the pool a group's documents use, and the group's own rows: the same rows for a group, its
    parent's then its own for a subgroup (each created if missing, so their counts can move)."""
    if group.parent_id is None:
        gss = _lock_group_services(db, group.id, keys)
        return gss, gss
    for g in (group.pool, group):
        for key in keys:
            svc.group_service(db, g, key, create=True)
    db.flush()
    return _lock_group_services(db, group.parent_id, keys), _lock_group_services(db, group.id, keys)


def _lock_user_services(db: Session, user: User, keys: list[str]) -> dict[str, UserService]:
    """The user's rows for these services, created if missing (a new row starts at zero)."""
    missing = [k for k in keys if user.service(k) is None]
    if missing:
        # Created with "insert ... on conflict do nothing": parallel requests for a new user don't collide
        db.execute(pg_insert(UserService).values([
            {"user_id": user.id, "service_key": k, "granted": False, "used_pages": 0, "reserved_pages": 0}
            for k in missing]).on_conflict_do_nothing())
        db.expire(user, ["services"])
    rows = db.scalars(select(UserService).where(UserService.user_id == user.id, UserService.service_key.in_(keys))
                      .order_by(UserService.service_key).with_for_update()
                      .execution_options(populate_existing=True)).all()
    return {r.service_key: r for r in rows}


def _entry(db: Session, *, service: str, scope: str, kind: str, pages: int, actor: Actor,
           gs: GroupService | None = None, s: Service | None = None, user: User | None = None,
           sub: GroupService | None = None,
           job_id: uuid.UUID | None = None, ticket_ref: str | None = None, ticket_url: str | None = None,
           override: bool = False, comment: str | None = None) -> QuotaLedger:
    pool, used, reserved = ((gs.pool_pages, gs.used_pages, gs.reserved_pages) if scope == "group"
                            else (s.pool_pages or 0, s.used_pages, s.reserved_pages))
    entry = QuotaLedger(
        group_id=gs.group_id if scope == "group" else None, user_id=user.id if user else None, job_id=job_id,
        subgroup_id=sub.group_id if sub is not None and sub is not gs else None,
        service_key=service, scope=scope, kind=kind, pages=pages, ticket_ref=ticket_ref, ticket_url=ticket_url,
        override=override, comment=comment, actor_id=actor.id, actor_email=actor.email, ip=actor.ip,
        pool_after=pool, used_after=used, reserved_after=reserved)
    db.add(entry)
    return entry


def group_remaining(gs: GroupService) -> int:
    return gs.pool_pages - gs.used_pages - gs.reserved_pages


def over_quota_message(db: Session, group: Group) -> tuple[str, str | None]:
    runtime = settings_store.load(db)
    return (group.over_quota_message or group.pool.over_quota_message or runtime.over_quota_message,
            runtime.support_url)


def check_access(db: Session, user: User, keys: list[str], pages: int) -> None:
    """Raise unless the user may use every one of these services for a document of this many pages."""
    for key in keys:
        a = svc.access(db, user, key)
        if not a.ok:
            raise ApiError(403, a.code, a.message)
        s = svc.get_service(db, key)
        if s.max_pages_per_job is not None and pages > s.max_pages_per_job:
            raise ApiError(422, "too_many_pages_for_service",
                           f"{s.label} takes documents of at most {s.max_pages_per_job} pages; this one has {pages}.")


def reserve(db: Session, user: User, pages: int, job_id: uuid.UUID, keys: list[str] | None = None) -> None:
    """Hold pages for a new job on each of its services, or raise. Caller commits."""
    keys = sorted(set(keys or [svc.STANDARD]))
    group = db.scalar(select(Group).where(Group.id == user.group_id))
    if group is None or group.status != "active":
        raise ApiError(403, "group_disabled", "Your group isn't active.")
    check_access(db, user, keys, pages)
    services = _lock_services(db, keys)
    gss, subs = _lock_pools(db, group, keys)
    uss = _lock_user_services(db, user, keys)
    msg, support = over_quota_message(db, group)
    whose = "Your group" if group.parent_id is None else f"{group.pool.name}, whose pages your group uses,"
    for key in keys:
        s, gs, us = services[key], gss[key], uss[key]
        if not s.metered:
            continue
        if s.pool_pages is not None and pages > s.pool_pages - s.used_pages - s.reserved_pages:
            raise OverQuota(f"{s.label} has used its pages for now. " + msg, "global",
                            max(0, s.pool_pages - s.used_pages - s.reserved_pages), pages, support, key)
        if pages > group_remaining(gs):
            left = max(0, group_remaining(gs))
            raise OverQuota(f"{whose} has {left:,} {s.label} page{'' if left == 1 else 's'} left and this document "
                            f"needs {pages:,}. " + msg, "group", left, pages, support, key)
        sub_left = svc.subgroup_left(subs[key]) if group.parent_id else None
        if sub_left is not None and pages > sub_left:
            left = max(0, sub_left)
            raise OverQuota(f"Your group has {left:,} {s.label} page{'' if left == 1 else 's'} left of its limit and "
                            f"this document needs {pages:,}. " + msg, "subgroup", left, pages, support, key)
        cap = svc.effective_cap(user, key)
        if cap is not None and pages > cap - us.used_pages - us.reserved_pages:
            left = max(0, cap - us.used_pages - us.reserved_pages)
            raise OverQuota(f"You have {left:,} {s.label} page{'' if left == 1 else 's'} left of your limit and this "
                            f"document needs {pages:,}. " + msg, "user", left, pages, support, key)
    actor = Actor(user.id, user.email)
    for key in keys:
        s, gs, sub, us = services[key], gss[key], subs[key], uss[key]
        s.reserved_pages += pages
        gs.reserved_pages += pages
        if sub is not gs:
            sub.reserved_pages += pages
        us.reserved_pages += pages
        _entry(db, service=key, scope="group", kind="reserve", pages=pages, actor=actor, gs=gs, user=user,
               job_id=job_id, sub=sub)


def _settle(db: Session, job: Job, kind: str, reason: str | None) -> None:
    keys = sorted(set(job.charged_services or []))
    if job.pages_charged <= 0 or job.group_id is None or not keys:
        return
    p = job.pages_charged
    services = _lock_services(db, keys)
    group = db.get(Group, job.group_id)
    gss, subs = (_lock_pools(db, group, keys) if group is not None and group.parent_id
                 else (_lock_group_services(db, job.group_id, keys),) * 2)
    user = db.get(User, job.user_id)
    uss = _lock_user_services(db, user, keys)
    for key in keys:
        s, gs, sub, us = services.get(key), gss.get(key), subs.get(key), uss[key]
        if s is None or gs is None:
            continue
        rows = [gs] if sub is None or sub is gs else [gs, sub]
        for row in (s, *rows, us):
            row.reserved_pages = max(0, row.reserved_pages - p)
            if kind == "commit":
                row.used_pages += p
        _entry(db, service=key, scope="group", kind=kind, pages=p, actor=SYSTEM, gs=gs, user=user, job_id=job.id,
               comment=reason, sub=sub)


def release_service(db: Session, job: Job, key: str, reason: str) -> None:
    """Give back one service's reservation for a job (e.g. its AI tier when every AI call failed); the job
    stays charged on its other services."""
    if key not in (job.charged_services or []) or job.pages_charged <= 0 or job.group_id is None:
        return
    others = [k for k in job.charged_services if k != key]
    job.charged_services = [key]
    _settle(db, job, "release", reason)
    job.charged_services = others


def commit(db: Session, job: Job) -> None:
    """Turn a job's reservations into usage (job completed)."""
    _settle(db, job, "commit", None)


def release(db: Session, job: Job, reason: str) -> None:
    """Give back a job's reservations (job failed or was canceled)."""
    _settle(db, job, "release", reason)
    job.pages_charged = 0


def _ticket(ticket_ref: str | None, override: bool, comment: str | None) -> tuple[str | None, str | None]:
    ticket_ref = (ticket_ref or "").strip() or None
    comment = (comment or "").strip() or None
    if ticket_ref is None and not override:
        raise ApiError(422, "ticket_required", "Enter the ticket number that approved these pages, "
                                               "or tick override and explain why.")
    if override and comment is None:
        raise ApiError(422, "comment_required", "An override needs a comment explaining why.")
    return ticket_ref, comment


def _service_or_404(db: Session, key: str, lock: bool = False) -> Service:
    s = _lock_services(db, [key]).get(key) if lock else svc.get_service(db, key)
    if s is None:
        raise ApiError(404, "not_found", "Service not found.")
    return s


def _pool_group(db: Session, group_id: uuid.UUID) -> Group:
    """A group whose pool pages may be added to or taken from: not a subgroup, whose pages are its parent's."""
    group = db.get(Group, group_id)
    if group is None:
        raise ApiError(404, "not_found", "Group not found.")
    if group.parent_id is not None:
        raise ApiError(422, "subgroup_pool", f"{group.name} uses {group.pool.name}'s pages. Add them to "
                                             f"{group.pool.name}, or change {group.name}'s limit.")
    return group


def set_subgroup_cap(db: Session, group: Group, cap: int | None, actor: Actor, *, comment: str | None = None,
                     service: str = svc.STANDARD) -> QuotaLedger:
    """Set (or, with None, take off) a subgroup's limit on its parent's pool for a service. It may go below what
    the subgroup has used: new documents wait for room then, and those already reserved carry on."""
    if group.parent_id is None:
        raise ApiError(422, "not_subgroup", "Only a subgroup has a limit on its parent's pages.")
    if cap is not None and cap < 0:
        raise ApiError(422, "invalid_pages", "Enter zero or more pages, or leave the limit empty.")
    _service_or_404(db, service, lock=True)
    pools, subs = _lock_pools(db, group, [service])
    sub = subs[service]
    change = (cap or 0) - (sub.cap_pages or 0)
    sub.cap_pages = cap
    return _entry(db, service=service, scope="group", kind="cap", pages=change, actor=actor, gs=pools[service],
                  sub=sub, comment=(comment or "").strip() or ("No limit" if cap is None else f"Limit {cap:,} pages"))


def topup(db: Session, group_id: uuid.UUID | None, pages: int, actor: Actor, *, ticket_ref: str | None,
          ticket_url: str | None, override: bool, comment: str | None, service: str = svc.STANDARD) -> QuotaLedger:
    """Add pages to a group's pool for a service, or to the service's global pool (group_id None)."""
    if pages <= 0:
        raise ApiError(422, "invalid_pages", "Enter a positive number of pages.")
    ticket_ref, comment = _ticket(ticket_ref, override, comment)
    s = _service_or_404(db, service, lock=True)
    if group_id is None:
        if s.pool_pages is None:
            raise ApiError(422, "unlimited", f"{s.label} has no global limit. Set one before adding pages to it.")
        s.pool_pages += pages
        return _entry(db, service=service, scope="global", kind="topup", pages=pages, actor=actor, s=s,
                      ticket_ref=ticket_ref, ticket_url=ticket_url, override=override, comment=comment)
    group = _pool_group(db, group_id)
    svc.group_service(db, group, service, create=True)
    db.flush()
    gs = _lock_group_services(db, group_id, [service])[service]
    gs.pool_pages += pages
    return _entry(db, service=service, scope="group", kind="topup", pages=pages, actor=actor, gs=gs,
                  ticket_ref=ticket_ref, ticket_url=ticket_url, override=override, comment=comment)


def adjust(db: Session, group_id: uuid.UUID | None, pages: int, actor: Actor, *, comment: str,
           ticket_ref: str | None, service: str = svc.STANDARD) -> QuotaLedger:
    """Correct a pool up or down. A pool can't go below what is already used or reserved from it."""
    comment = (comment or "").strip()
    if not comment:
        raise ApiError(422, "comment_required", "An adjustment needs a comment explaining why.")
    if pages == 0:
        raise ApiError(422, "invalid_pages", "Enter a non-zero number of pages.")
    ticket_ref = (ticket_ref or "").strip() or None
    s = _service_or_404(db, service, lock=True)
    if group_id is None:
        if s.pool_pages is None:
            raise ApiError(422, "unlimited", f"{s.label} has no global limit to adjust.")
        if s.pool_pages + pages < s.used_pages + s.reserved_pages:
            raise ApiError(422, "below_usage", f"The pool can't go below the {s.used_pages + s.reserved_pages} "
                                               "pages already used or reserved.")
        s.pool_pages += pages
        return _entry(db, service=service, scope="global", kind="adjust", pages=pages, actor=actor, s=s,
                      ticket_ref=ticket_ref, comment=comment)
    group = _pool_group(db, group_id)
    svc.group_service(db, group, service, create=True)
    db.flush()
    gs = _lock_group_services(db, group_id, [service])[service]
    if gs.pool_pages + pages < gs.used_pages + gs.reserved_pages:
        raise ApiError(422, "below_usage",
                       f"The pool can't go below the {gs.used_pages + gs.reserved_pages} pages already used or reserved.")
    gs.pool_pages += pages
    return _entry(db, service=service, scope="group", kind="adjust", pages=pages, actor=actor, gs=gs,
                  ticket_ref=ticket_ref, comment=comment)


def correct_usage(db: Session, user: User, pages: int, actor: Actor, *, comment: str, ticket_ref: str | None,
                  service: str = svc.STANDARD) -> tuple[QuotaLedger, int, int]:
    """A super admin corrects one person's usage of a service: negative pages give pages back (after a bad
    run, say), positive pages charge more. The person's, the group's and the service's used counts move
    together, so the pools stay consistent. Returns the ledger entry and the person's used pages before
    and after."""
    comment = (comment or "").strip()
    if not comment:
        raise ApiError(422, "comment_required", "A correction needs a reason.")
    if pages == 0:
        raise ApiError(422, "invalid_pages", "Enter a non-zero number of pages.")
    if user.group_id is None:
        raise ApiError(422, "no_group", "This person isn't in a group, so they have no usage to correct.")
    s = _service_or_404(db, service, lock=True)
    group = db.get(Group, user.group_id)
    gss, subs = _lock_pools(db, group, [service])
    if group.parent_id is None:
        svc.group_service(db, group, service, create=True)
        db.flush()
        gss = subs = _lock_group_services(db, group.id, [service])
    gs, sub = gss[service], subs[service]
    us = _lock_user_services(db, user, [service])[service]
    before = us.used_pages
    if pages < 0 and -pages > us.used_pages:
        raise ApiError(422, "below_zero", f"{user.email} has used {us.used_pages} pages of {s.label}; "
                                          f"you can give back at most that many.")
    if pages > 0 and s.metered and pages > group_remaining(gs):
        raise ApiError(422, "over_pool", f"The group has only {max(0, group_remaining(gs))} pages of {s.label} left "
                                         "to charge. Add pages to its pool first.")
    us.used_pages += pages
    gs.used_pages = max(0, gs.used_pages + pages)
    if sub is not gs:
        sub.used_pages = max(0, sub.used_pages + pages)
    s.used_pages = max(0, s.used_pages + pages)
    entry = _entry(db, service=service, scope="group", kind="correct", pages=pages, actor=actor, gs=gs, user=user,
                   ticket_ref=(ticket_ref or "").strip() or None, comment=comment, sub=sub)
    return entry, before, us.used_pages


def set_global_limit(db: Session, key: str, limited: bool) -> Service:
    """Switch a service's global pool on (starting with nothing left to spend) or off (no global limit)."""
    s = _service_or_404(db, key, lock=True)
    if limited and s.pool_pages is None:
        s.pool_pages = s.used_pages + s.reserved_pages
    elif not limited:
        s.pool_pages = None
    return s


def pages_sent_today(db: Session, key: str) -> int:
    """Pages of jobs using a service that were sent for processing since midnight UTC (its daily cap)."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(select(func.coalesce(func.sum(Job.pages_charged), 0)).where(
        Job.submitted_at >= start, Job.charged_services.contains([key]))) or 0)


def active_jobs(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Job).where(
        Job.user_id == user_id, Job.status.in_(ACTIVE_JOB_STATUSES))) or 0
