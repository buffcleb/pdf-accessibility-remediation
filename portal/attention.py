"""What needs an administrator's attention, and why a person can or can't use a service.

Both answer questions admins kept having to work out by hand: "why does this person see 0 pages left?"
(a personal limit with an empty group pool, twice in testing) and "is anything stuck or about to run out?".
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from portal import queues, quota, services as svc, settings_store
from portal.models import Group, Job, MailOutbox, Service, User

RECENT = timedelta(days=7)
STUCK = timedelta(hours=2)
LARGE_WAIT = timedelta(hours=2)
HELD_LONG = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- why can't this person use it? ------------------------------------------------------------------

@dataclass
class Step:
    key: str
    label: str
    ok: bool
    detail: str
    fix: str | None = None  # what to do, in words; the web app links to the place
    blocking: bool = True


@dataclass
class AccessCheck:
    user_email: str
    service_key: str
    service_label: str
    can_use: bool
    remaining_pages: int | None  # None: no page limit
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def explain_access(db: Session, user: User, service: Service) -> AccessCheck:
    """Every check between a person and a service, in the order they apply."""
    steps: list[Step] = []
    group: Group | None = user.group
    gs = group.service(service.key) if group else None
    us = user.service(service.key)

    def add(key, label, ok, detail, fix=None, blocking=True):
        steps.append(Step(key, label, bool(ok), detail, None if ok else fix, blocking))

    add("account", "Account active", user.status == "active",
        "active" if user.status == "active" else f"the account is {user.status}",
        "Re-enable the account (the group's People tab, or People (all groups)).")
    add("approved", "Approved by the group", user.approved,
        "approved" if user.approval == "approved" else ("an admin" if user.approved else
                                                        "declined" if user.approval == "declined" else "waiting for approval"),
        "Approve them (the group's People tab).")
    add("group", "In an active group", group is not None and group.status == "active",
        f"{group.name}" if group is not None and group.status == "active"
        else ("not in a group" if group is None else f"{group.name} is {group.status}"),
        "Move the person into a group (People (all groups)), or re-enable the group (Groups).")
    add("service_on", f"{service.label} switched on", service.enabled,
        "switched on for the service" if service.enabled else "switched off for everyone",
        f"Switch {service.label} on (Services and AI levels).")
    if group is not None:
        local_block = service.external and group.local_only
        add("local_only", "Allowed by the group's data rules", not local_block,
            "the group keeps documents local, and this provider is external" if local_block
            else ("external provider allowed" if service.external else "runs on this service's own servers"),
            "Turn off local-only for the group, if its documents may go to this provider (the group's Group settings).")
        add("group_enabled", f"Enabled for {group.name}", gs is not None and gs.enabled,
            "enabled" if gs is not None and gs.enabled else "not enabled for the group",
            f"Enable {service.label} for the group (the group's Services and pages).")
        granted = bool(gs and (gs.all_users or (us is not None and us.granted)))
        add("granted", "Given to this person", granted,
            ("everyone in the group has it" if gs and gs.all_users else "given to them individually") if granted
            else "the group gives it to chosen people only, and not to them",
            "Give it to everyone in the group (Services and pages), or to this person (People).")

    if service.metered and group is not None:
        if service.pool_pages is not None:
            left = service.pool_pages - service.used_pages - service.reserved_pages
            add("global_pages", "Pages left for the whole service", left > 0,
                f"{max(0, left):,} of {service.pool_pages:,} left",
                f"Add pages to {service.label}'s global pool (Services and AI levels → {service.label} → Pages).")
        if gs is not None:
            left = quota.group_remaining(gs)
            add("group_pages", f"Pages left in {group.name}'s pool", left > 0,
                f"{max(0, left):,} of {gs.pool_pages:,} left" + ("" if left > 0 else
                                                                   ": personal limits can't give pages the group hasn't got"),
                f"Add {service.label} pages to the group, or request them (Groups → {group.name} → Services and pages).")
        cap = svc.effective_cap(user, service.key)
        if cap is not None:
            used = (us.used_pages + us.reserved_pages) if us else 0
            own = us is not None and us.cap_pages is not None
            add("person_pages", "Within their own limit", cap - used > 0,
                f"{max(0, cap - used):,} of {cap:,} left ({'their own limit' if own else 'the group default'})",
                "Raise their limit (the group's People tab), or correct their usage (People (all groups)).")
        else:
            add("person_pages", "Within their own limit", True, "no personal limit")
    elif not service.metered:
        add("pages", "Page limits", True, "use is counted but never limited")

    if service.max_pages_per_job is not None:
        add("per_document", "Longest document allowed", True,
            f"documents of up to {service.max_pages_per_job:,} pages", blocking=False)
    if service.daily_cap_pages is not None:
        sent = quota.pages_sent_today(db, service.key)
        add("daily_cap", "Daily limit", sent < service.daily_cap_pages,
            f"{sent:,} of {service.daily_cap_pages:,} pages sent today"
            + ("" if sent < service.daily_cap_pages else ": new documents wait until tomorrow"),
            "Raise the daily limit (Services and AI levels), or wait for tomorrow.", blocking=False)
    can_use = all(s.ok for s in steps if s.blocking)
    remaining = svc.remaining(user, service.key, service) if can_use else 0
    return AccessCheck(user.email, service.key, service.label, can_use, remaining, steps)


# --- what needs attention -----------------------------------------------------------------------------

@dataclass
class Item:
    severity: str  # bad | warn | info
    kind: str
    title: str
    detail: str
    group_id: str | None = None
    group_name: str | None = None
    service_key: str | None = None
    count: int | None = None
    fix: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _low(left: int, pool: int, rs) -> bool:
    """Running low (Settings: low_pool_percent, low_pool_pages), as the email to the group's admins counts it."""
    from portal.pool_alerts import is_low
    return is_low(left, pool, rs)


def attention(db: Session, group_ids: list[uuid.UUID] | None = None) -> list[Item]:
    """Items for these groups (None: every group, plus service-wide items), most serious first."""
    items: list[Item] = []
    rs = settings_store.load(db)
    if group_ids is None:  # the service's own problems (#334), as emailed to super admins
        from portal import alerts
        for a in alerts.active(db):
            items.append(Item("bad", a.key, alerts.title(a.key), a.detail, fix=alerts.fix(a.key)))
    groups = list(db.scalars(select(Group).where(Group.status == "active")
                             .where(Group.id.in_(group_ids)) if group_ids is not None
                             else select(Group).where(Group.status == "active")))
    services = {s.key: s for s in svc.all_services(db)}

    if group_ids is None:
        for s in services.values():
            if not s.enabled or not s.metered or s.pool_pages is None:
                continue
            left = s.pool_pages - s.used_pages - s.reserved_pages
            if left <= 0:
                items.append(Item("bad", "global_pool_empty", f"{s.label}: no pages left for the whole service",
                                  f"0 of {s.pool_pages:,} left; nobody can use it until pages are added.",
                                  service_key=s.key, fix="Add pages to the service's global pool (Services)."))
            elif _low(left, s.pool_pages, rs):
                items.append(Item("warn", "global_pool_low", f"{s.label}: running low for the whole service",
                                  f"{left:,} of {s.pool_pages:,} pages left.", service_key=s.key,
                                  fix="Add pages to the service's global pool (Services)."))

    for g in groups:
        waiting = db.scalar(select(func.count()).select_from(User).where(
            User.group_id == g.id, User.status == "active", User.approval == "pending")) or 0
        if waiting:
            items.append(Item("warn", "members_waiting",
                              f"{g.name}: {waiting} {'person' if waiting == 1 else 'people'} waiting for approval",
                              "They can sign in but can't submit documents until someone approves them.",
                              str(g.id), g.name, count=waiting, fix="Approve or decline them (the group's People tab)."))
        members = db.scalar(select(func.count()).select_from(User).where(User.group_id == g.id,
                                                                          User.status == "active")) or 0
        for gs in g.services:
            s = services.get(gs.service_key)
            if s is None or not s.enabled or not gs.enabled or not s.metered:
                continue
            users_with = members if gs.all_users else sum(1 for u in db.scalars(select(User).where(
                User.group_id == g.id, User.status == "active")) if (us := u.service(s.key)) and us.granted)
            if users_with == 0:
                continue
            left = quota.group_remaining(gs)
            who = f"{users_with} {'person' if users_with == 1 else 'people'} can use it"
            if left <= 0:
                items.append(Item("bad", "group_pool_empty", f"{g.name}: no {s.label} pages left",
                                  f"{who}, but the group's pool is empty ({gs.pool_pages:,} pages, all used or "
                                  "held). Personal limits don't add pages, so they all see 0 left.",
                                  str(g.id), g.name, s.key, fix="Add pages to the group, or request them (its Services and pages)."))
            elif _low(left, gs.pool_pages, rs):
                items.append(Item("warn", "group_pool_low", f"{g.name}: {s.label} running low",
                                  f"{left:,} of {gs.pool_pages:,} pages left; {who}.", str(g.id), g.name, s.key,
                                  fix="Add pages to the group, or request them (its Services and pages)."))
            # Personal limits that promise more than the pool holds
            if left > 0:
                over = 0
                for u in db.scalars(select(User).where(User.group_id == g.id, User.status == "active")):
                    cap = svc.effective_cap(u, s.key)
                    us = u.service(s.key)
                    if not (gs.all_users or (us and us.granted)):
                        continue
                    own_left = None if cap is None else cap - ((us.used_pages + us.reserved_pages) if us else 0)
                    if own_left is None or own_left > left:
                        over += 1
                if over and _low(left, gs.pool_pages, rs):
                    items.append(Item("info", "limits_exceed_pool", f"{g.name}: {s.label} limits exceed the pool",
                                      f"{over} {'person has' if over == 1 else 'people have'} a limit larger than the "
                                      f"{left:,} pages left in the group's pool; the pool runs out first.",
                                      str(g.id), g.name, s.key, over, "Add pages to the group, or lower the limits."))

    gid = [g.id for g in groups]
    job_scope = Job.group_id.in_(gid) if group_ids is not None else true()
    now = _now()

    def count(*where) -> int:
        return db.scalar(select(func.count()).select_from(Job).where(job_scope, *where)) or 0

    if n := count(Job.status == "held", Job.created_at < now - HELD_LONG):
        items.append(Item("info", "held", f"{n} document{'s' if n > 1 else ''} waiting for confirmation",
                          "Held for more than a day: the person hasn't sent them yet. Nothing is charged until they do.",
                          count=n))
    waiting = [j for j in db.scalars(select(Job).where(job_scope, Job.status == "queued"))
               if (j.progress or {}).get("stage") == "waiting_daily_cap"]
    if waiting:
        keys = sorted({k for j in waiting for k in (j.progress or {}).get("services") or []})
        items.append(Item("warn", "daily_cap", f"{len(waiting)} document{'s' if len(waiting) > 1 else ''} waiting "
                          "for tomorrow's daily limit", f"Held back by the daily limit of: {', '.join(keys)}.",
                          count=len(waiting), fix="Raise the daily limit if they shouldn't wait (Services)."))
    # Paused queues: everyone sees the documents held by a paused service; super admins also see every paused queue
    pauses = queues.paused(db)
    if pauses:
        held = [j for j in db.scalars(select(Job).where(job_scope, Job.status == "queued"))
                if queues.paused_services(j, pauses)]
        labels = {d.key: d.label for d in queues.definitions(db)}
        if group_ids is None:
            for key, p in sorted(pauses.items(), key=lambda kv: kv[1].paused_at):
                items.append(Item("warn", "queue_paused", f"{labels.get(key, key)} is paused",
                                  f"Paused by {p.paused_by} on {p.paused_at:%Y-%m-%d %H:%M} UTC"
                                  + (f": {p.note}" if p.note else "") + ". Nothing new starts until it's resumed.",
                                  fix="Resume it in Queues when it's ready."))
        elif held:
            items.append(Item("info", "queue_paused", f"{len(held)} document{'s' if len(held) > 1 else ''} waiting "
                              "for a paused service", "Support has paused a processing option these documents use. "
                              "They'll go ahead when it's resumed; nothing more is needed.", count=len(held)))
    if group_ids is None:
        if n := db.scalar(select(func.count()).select_from(MailOutbox).where(
                MailOutbox.status == "failed", MailOutbox.created_at > now - RECENT)):
            items.append(Item("warn", "mail_failed", f"{n} email{'s' if n > 1 else ''} couldn't be sent in the last 7 days",
                              "The mail provider refused them or kept failing.", count=n,
                              fix="See why and try them again in Queues → Email."))
    large_waiting = [j for j in db.scalars(select(Job).where(job_scope, Job.status == "queued",
                                                             Job.created_at < now - LARGE_WAIT))
                     if (j.progress or {}).get("stage") == "waiting_large"]
    if large_waiting:
        n = len(large_waiting)
        items.append(Item("info", "large_waiting", f"{n} large document{'s' if n > 1 else ''} waiting over "
                          f"{LARGE_WAIT.seconds // 3600} hours", "Large documents start after every few smaller ones, "
                          "or when nothing smaller is waiting. A busy day can hold them back.", count=n,
                          fix="Super admins can use Process now in Queues if one is urgent." if group_ids is None
                          else "Ask support if one is urgent."))
    # A worker lost mid-way (#315): the engine will try once more after its timeout, or fail it
    lost = [j for j in db.scalars(select(Job).where(job_scope, Job.status.in_(("submitted", "processing"))))
            if (j.progress or {}).get("stage") == "worker_lost"]
    if lost:
        n = len(lost)
        items.append(Item("warn", "worker_lost", f"{n} document{'s' if n > 1 else ''} lost {'their' if n > 1 else 'its'} "
                          "engine worker partway through",
                          "The worker stopped while processing (often running out of memory on a large document). "
                          "Each is tried once more after the task time limit, then fails with its pages given back.",
                          count=n, fix="Check the engine worker's memory limit and logs (operations guide: Monitoring)."
                          if group_ids is None else "Nothing to do: support is told."))
    if n := count(Job.status.in_(("submitted", "processing")), Job.submitted_at < now - STUCK):
        items.append(Item("warn", "stuck", f"{n} document{'s' if n > 1 else ''} processing for over 2 hours",
                          "Usually a stopped engine worker or an unreachable AI or Adobe service.", count=n,
                          fix="Check the engine's workers and logs (operations guide: Monitoring)."))
    failed = list(db.execute(select(Job.error_code, func.count()).where(
        job_scope, Job.status == "failed", Job.finished_at > now - RECENT).group_by(Job.error_code)))
    if failed:
        total = sum(n for _, n in failed)
        top = ", ".join(f"{code or 'unknown'} ({n})" for code, n in sorted(failed, key=lambda r: -r[1])[:4])
        items.append(Item("warn" if total >= 3 else "info", "failed",
                          f"{total} document{'s' if total > 1 else ''} failed in the last 7 days", f"Reasons: {top}.",
                          count=total, fix="Open the documents to see why (Documents, filtered by failed)."))
    refunds = [j for j in db.scalars(select(Job).where(job_scope, Job.status == "completed",
                                                       Job.finished_at > now - RECENT))
               if (j.cost or {}).get("ai_refunded") and not (j.cost or {}).get("ai_unused")]
    if refunds:
        tiers = sorted({j.ai_tier or "?" for j in refunds})
        items.append(Item("warn", "ai_unavailable", f"The AI wasn't available for {len(refunds)} "
                          f"document{'s' if len(refunds) > 1 else ''} in the last 7 days",
                          f"Every AI call failed, so they used layout rules only (AI pages given back). "
                          f"AI levels: {', '.join(tiers)}.", count=len(refunds),
                          fix="Check the AI gateway and its models (Administration → AI gateway)."))
    order = {"bad": 0, "warn": 1, "info": 2}
    items.sort(key=lambda i: (order[i.severity], i.group_name or "", i.title))
    return items
