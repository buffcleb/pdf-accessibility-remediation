"""What needs an administrator's attention, and why a person can or can't use a service.

Both answer questions admins kept having to work out by hand: "why does this person see 0 pages left?"
(a personal limit with an empty group pool, twice in testing) and "is anything stuck or about to run out?".
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

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
        if (pl := svc.pool_left(group, service.key)) is not None:
            size, left = pl  # a subgroup's: its parent's pool, within its own limit (#431)
            parent = group.pool if group.parent_id else None
            add("group_pages", f"Pages left for {group.name}, from {parent.name}'s pool" if parent
                else f"Pages left in {group.name}'s pool", left > 0,
                f"{max(0, left):,} of {size:,} left" + ("" if left > 0 else
                                                                   ": personal limits can't give pages the group hasn't got"),
                f"Add {service.label} pages to {parent.name}, or raise {group.name}'s limit (Groups → {group.name} → "
                "Services and pages)." if parent else
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
    # What it's about, in the app (#538): one thing goes straight to it (a document, or its open help request),
    # several to the list of them. Only pages the viewer can open: a group's admins never get a super admin's page.
    link: str | None = None
    link_label: str | None = None
    # Acknowledging (#538): a summary or notice can be, for everyone who sees the list, until something new joins it.
    # `ids`: what it's about now (documents, emails, an alert); `earlier`: how many were acknowledged before. Live
    # problems (the service's alerts, empty pools, people waiting) can't be: they go when they're fixed.
    key: str | None = None
    ackable: bool = False
    earlier: int = 0
    ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and k != "ids" and not (k == "earlier" and not v)}


def _low(left: int, pool: int, rs) -> bool:
    """Running low (Settings: low_pool_percent, low_pool_pages), as the email to the group's admins counts it."""
    from portal.pool_alerts import is_low
    return is_low(left, pool, rs)


# Why a document failed, as an admin reads it (the person sees the longer message, portal/worker.py)
REASONS = {
    "adobe_rejected": "Adobe refused the PDF", "adobe_failed": "Adobe didn't finish", "adobe_quota": "Adobe's usage limit",
    "adobe_unavailable": "Adobe unavailable", "engine_rejected": "the engine refused it",
    "engine_lost": "the engine lost track of it", "worker_lost": "the engine's worker stopped",
    "time_limit_exceeded": "it took too long", "tag_integrity_failed": "no valid file could be made",
    "ai_provider_unavailable": "the AI was unavailable", "result_not_saved": "its result couldn't be saved",
    "stopped": "it was stopped",
}
# A service alert's page: where to look (Administration > System unless another says more)
ALERT_LINKS = {"queue_stuck": ("/admin/queues", "Open Queues"),
               "siem_behind": ("/admin/system/audit-export", "Open Audit export")}
# A job's status as the group's document list filters it, and as the Documents report does
_LIST_STATUS = {"failed": "failed", "held": "held", "queued": "waiting", "submitted": "in_progress",
                "processing": "in_progress", "completed": "done"}


def _waited(hours: float) -> str:
    if hours < 1:
        return "less than an hour"
    if hours < 48:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"
    return f"{int(hours // 24)} days"


def _reason(code: str | None) -> str:
    return REASONS.get(code or "", code or "unknown")


def _alert_link(key: str) -> tuple[str, str]:
    return ALERT_LINKS.get(key, ("/admin/system", "Open System"))


def _documents_link(db: Session, jobs: list[Job], everyone: bool, days: int | None = None,
                    live: str | None = None) -> tuple[str | None, str | None]:
    """Where to see these documents: one, on its page (its help request's, when one is open); several of one group,
    on the group's documents filtered to their status (and the last `days` days); several of several groups, for a
    super admin (`everyone`), the live queue view (`live`: its state) or the Documents report."""
    if not jobs:
        return None, None
    if len(jobs) == 1:
        from portal.models import HelpRequest
        req = db.scalar(select(HelpRequest.id).where(HelpRequest.job_id == jobs[0].id, HelpRequest.status.in_(
            ("open", "in_progress", "waiting"))).limit(1))
        return (f"/admin/help/{req}", "Open its help request") if req else (f"/jobs/{jobs[0].id}", "Open the document")
    statuses = {j.status for j in jobs}
    groups = {j.group_id for j in jobs}
    if len(groups) == 1 and not (everyone and live):
        q = f"status={_LIST_STATUS.get(statuses.pop(), '')}" if len(statuses) == 1 else ""
        if days:
            since = (_now() - timedelta(days=days - 1)).date().isoformat()
            q += ("&" if q else "") + f"from={since}"
        return f"/admin/groups/{groups.pop()}/documents" + (f"?{q}" if q else ""), "Show them"
    if not everyone:
        return None, None  # a group admin of several groups: each group's own list
    if live:
        return f"/admin/queues?view=live&state={live}", "Show them in Queues"
    q = f"status={statuses.pop()}&" if len(statuses) == 1 else ""
    return f"/admin/reports?report=documents&{q}range={days or 30}d", "Show them in the Documents report"


class Family(list):
    """A group and its subgroups (#431): one group's list, acknowledged as that group's (its first id)."""


def scope_of(group_ids: list[uuid.UUID] | None) -> str | None:
    """Whose list it is, for acknowledging: super admins' ("all"), or one group's admins' (its id; with its
    subgroups, a Family)."""
    if group_ids is None:
        return "all"
    return str(group_ids[0]) if len(group_ids) == 1 or isinstance(group_ids, Family) else None


def attention(db: Session, group_ids: list[uuid.UUID] | None = None, with_acknowledged: bool = False) -> list[Item]:
    """Items for these groups (None: every group, plus service-wide items), most serious first. Acknowledged ones
    (#538) count only what's new since; with_acknowledged, they're counted in full as if never acknowledged (for
    acknowledging them again)."""
    items: list[Item] = []
    rs = settings_store.load(db)
    from portal.models import AttentionAck
    scope = scope_of(group_ids)
    acked: dict[str, set[str]] = {} if scope is None or with_acknowledged else {
        a.key: set(a.ids or []) for a in db.scalars(select(AttentionAck).where(AttentionAck.scope == scope))}

    def fresh(key: str, things: list, id_of=lambda t: str(t.id)) -> tuple[list, int]:
        """What's new since this item was acknowledged, and how many were acknowledged before."""
        seen = acked.get(key, set())
        new = [t for t in things if id_of(t) not in seen]
        return new, len(things) - len(new)
    if group_ids is None:  # the service's own problems (#334), as emailed to super admins
        from portal import alerts
        for a in alerts.active(db):
            link, label = _alert_link(a.key)
            items.append(Item("bad", a.key, alerts.title(a.key), a.detail, fix=alerts.fix(a.key), link=link,
                              link_label=label))
        from portal import security_alerts  # patterns in the audit log, for a day (#456)
        for ev in security_alerts.recent(db):
            if str(ev.id) in acked.get(f"security:{ev.id}", set()):
                continue
            d = ev.details or {}
            rule = d.get("rule", "")
            search = d.get("search") or ("" if str(d.get("subject", "")).startswith("event:") else d.get("subject", ""))
            items.append(Item("warn", f"security:{rule}", security_alerts.TITLES.get(rule, "Security alert"),
                              d.get("detail", ""), group_id=str(ev.group_id) if ev.group_id else None,
                              fix=security_alerts.FIXES.get(rule),
                              link=f"/admin/reports?report=audit&range=7d&q={quote(search)}" if search
                              else "/admin/reports?report=audit&range=7d", link_label="Show it in the audit log",
                              key=f"security:{ev.id}", ackable=True, ids=[str(ev.id)]))
        # Help requests waiting for support (#538): new, or the person's last word unanswered
        from portal import helpdesk
        from portal.models import HelpRequest
        waiting_help = db.execute(select(HelpRequest.id, helpdesk.WAITING_SINCE).where(
            helpdesk.NEEDS_STAFF, HelpRequest.saved.is_(False)).order_by(helpdesk.WAITING_SINCE)).all()
        if waiting_help:
            n, oldest = len(waiting_help), waiting_help[0][1]
            hours = (_now() - oldest).total_seconds() / 3600 if oldest else 0
            items.append(Item("warn" if hours >= 24 else "info", "help_waiting",
                              f"{n} help request{'s' if n > 1 else ''} waiting for support",
                              f"The oldest has waited {_waited(hours)}.", count=n,
                              fix="Answer them, or set them in progress (Help requests).",
                              link=f"/admin/help/{waiting_help[0][0]}" if n == 1 else "/admin/help",
                              link_label="Open the help request" if n == 1 else "Open Help requests"))
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
                                  service_key=s.key, fix="Add pages to the service's global pool (Services).",
                                  link=f"/admin/services/{s.key}", link_label="Open the service"))
            elif _low(left, s.pool_pages, rs):
                items.append(Item("warn", "global_pool_low", f"{s.label}: running low for the whole service",
                                  f"{left:,} of {s.pool_pages:,} pages left.", service_key=s.key,
                                  fix="Add pages to the service's global pool (Services).",
                                  link=f"/admin/services/{s.key}", link_label="Open the service"))

    for g in groups:
        waiting = db.scalar(select(func.count()).select_from(User).where(
            User.group_id == g.id, User.status == "active", User.approval == "pending")) or 0
        if waiting:
            items.append(Item("warn", "members_waiting",
                              f"{g.name}: {waiting} {'person' if waiting == 1 else 'people'} waiting for approval",
                              "They can sign in but can't submit documents until someone approves them.",
                              str(g.id), g.name, count=waiting, fix="Approve or decline them (the group's People tab).",
                              link=f"/admin/groups/{g.id}/people", link_label="Open People"))
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
            size, left = svc.pool_left(g, s.key) or (0, 0)  # a subgroup's: its parent's, within its limit
            who = f"{users_with} {'person' if users_with == 1 else 'people'} can use it"
            if left <= 0:
                items.append(Item("bad", "group_pool_empty", f"{g.name}: no {s.label} pages left",
                                  f"{who}, but the group's pool is empty ({size:,} pages, all used or "
                                  "held). Personal limits don't add pages, so they all see 0 left.",
                                  str(g.id), g.name, s.key, fix="Add pages to the group, or request them (its Services and pages).",
                                  link=f"/admin/groups/{g.id}/services", link_label="Open Services and pages"))
            elif _low(left, size, rs):
                items.append(Item("warn", "group_pool_low", f"{g.name}: {s.label} running low",
                                  f"{left:,} of {size:,} pages left; {who}.", str(g.id), g.name, s.key,
                                  fix="Add pages to the group, or request them (its Services and pages).",
                                  link=f"/admin/groups/{g.id}/services", link_label="Open Services and pages"))
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
                if over and _low(left, size, rs):
                    items.append(Item("info", "limits_exceed_pool", f"{g.name}: {s.label} limits exceed the pool",
                                      f"{over} {'person has' if over == 1 else 'people have'} a limit larger than the "
                                      f"{left:,} pages left in the group's pool; the pool runs out first.",
                                      str(g.id), g.name, s.key, over, "Add pages to the group, or lower the limits.",
                                      link=f"/admin/groups/{g.id}/services", link_label="Open Services and pages"))

    gid = [g.id for g in groups]
    job_scope = Job.group_id.in_(gid) if group_ids is not None else true()
    now = _now()

    everyone = group_ids is None
    held_long, earlier = fresh("held", db.scalars(select(Job).where(
        job_scope, Job.status == "held", Job.created_at < now - HELD_LONG)).all())
    if n := len(held_long):
        link, label = _documents_link(db, held_long, everyone)
        items.append(Item("info", "held", f"{n} document{'s' if n > 1 else ''} waiting for confirmation",
                          "Held for more than a day: the person hasn't sent them yet. Nothing is charged until they do.",
                          count=n, link=link, link_label=label, key="held", ackable=True, earlier=earlier,
                          ids=[str(j.id) for j in held_long]))
    waiting = [j for j in db.scalars(select(Job).where(job_scope, Job.status == "queued"))
               if (j.progress or {}).get("stage") == "waiting_daily_cap"]
    if waiting:
        keys = sorted({k for j in waiting for k in (j.progress or {}).get("services") or []})
        link, label = (f"/admin/services/{keys[0]}", "Open the service") if everyone and len(keys) == 1 \
            else _documents_link(db, waiting, everyone, live="waiting")
        items.append(Item("warn", "daily_cap", f"{len(waiting)} document{'s' if len(waiting) > 1 else ''} waiting "
                          "for tomorrow's daily limit", f"Held back by the daily limit of: {', '.join(keys)}.",
                          count=len(waiting), fix="Raise the daily limit if they shouldn't wait (Services).", link=link,
                          link_label=label))
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
                                  fix="Resume it in Queues when it's ready.", link=f"/admin/queues?queue={key}",
                                  link_label="Open the queue"))
        elif held:
            link, label = _documents_link(db, held, everyone)
            items.append(Item("info", "queue_paused", f"{len(held)} document{'s' if len(held) > 1 else ''} waiting "
                              "for a paused service", "Support has paused a processing option these documents use. "
                              "They'll go ahead when it's resumed; nothing more is needed.", count=len(held), link=link,
                              link_label=label))
    if group_ids is None:
        failed_mail, earlier = fresh("mail_failed", db.scalars(select(MailOutbox).where(
            MailOutbox.status == "failed", MailOutbox.created_at > now - RECENT)).all())
        if n := len(failed_mail):
            items.append(Item("warn", "mail_failed", f"{n} email{'s' if n > 1 else ''} couldn't be sent in the last 7 days",
                              "The mail provider refused them or kept failing.", count=n,
                              fix="See why and try them again in Queues → Email.",
                              link=f"/admin/queues?queue={queues.MAIL}&state=failed", link_label="Show them",
                              key="mail_failed", ackable=True, earlier=earlier, ids=[str(m.id) for m in failed_mail]))
    large_waiting, large_earlier = fresh("large_waiting", [
        j for j in db.scalars(select(Job).where(job_scope, Job.status == "queued", Job.created_at < now - LARGE_WAIT))
        if (j.progress or {}).get("stage") == "waiting_large"])
    if large_waiting:
        n = len(large_waiting)
        link, label = _documents_link(db, large_waiting, everyone, live="waiting")
        items.append(Item("info", "large_waiting", f"{n} large document{'s' if n > 1 else ''} waiting over "
                          f"{LARGE_WAIT.seconds // 3600} hours", "Large documents start after every few smaller ones, "
                          "or when nothing smaller is waiting. A busy day can hold them back.", count=n,
                          fix="Super admins can use Process now in Queues if one is urgent." if group_ids is None
                          else "Ask support if one is urgent.", link=link, link_label=label, key="large_waiting",
                          ackable=True, earlier=large_earlier, ids=[str(j.id) for j in large_waiting]))
    # A worker lost mid-way (#315): the engine will try once more after its timeout, or fail it
    lost, lost_earlier = fresh("worker_lost", [
        j for j in db.scalars(select(Job).where(job_scope, Job.status.in_(("submitted", "processing"))))
        if (j.progress or {}).get("stage") == "worker_lost"])
    if lost:
        n = len(lost)
        link, label = _documents_link(db, lost, everyone, live="active")
        items.append(Item("warn", "worker_lost", f"{n} document{'s' if n > 1 else ''} lost {'their' if n > 1 else 'its'} "
                          "engine worker partway through",
                          "The worker stopped while processing (often running out of memory on a large document). "
                          "Each is tried once more after the task time limit, then fails with its pages given back.",
                          count=n, fix="Check the engine worker's memory limit and logs (operations guide: Monitoring)."
                          if group_ids is None else "Nothing to do: support is told.", link=link, link_label=label,
                          key="worker_lost", ackable=True, earlier=lost_earlier, ids=[str(j.id) for j in lost]))
    stuck = db.scalars(select(Job).where(job_scope, Job.status.in_(("submitted", "processing")),
                                         Job.submitted_at < now - STUCK)).all()
    if n := len(stuck):
        link, label = _documents_link(db, stuck, everyone, live="active")
        items.append(Item("warn", "stuck", f"{n} document{'s' if n > 1 else ''} processing for over 2 hours",
                          "Usually a stopped engine worker or an unreachable AI or Adobe service.", count=n,
                          fix="Check the engine's workers and logs (operations guide: Monitoring).", link=link,
                          link_label=label))
    failed_jobs, failed_earlier = fresh("failed", db.scalars(select(Job).where(
        job_scope, Job.status == "failed", Job.finished_at > now - RECENT)).all())
    if failed_jobs:
        total, by_code = len(failed_jobs), {}
        for j in failed_jobs:
            by_code[j.error_code] = by_code.get(j.error_code, 0) + 1
        top = ", ".join(f"{_reason(code)} ({n})" for code, n in sorted(by_code.items(), key=lambda r: -r[1])[:4])
        link, label = _documents_link(db, failed_jobs, everyone, days=7)
        items.append(Item("warn" if total >= 3 else "info", "failed",
                          f"{total} document{'s' if total > 1 else ''} failed in the last 7 days", f"Reasons: {top}.",
                          count=total, fix="Open the document to see why." if total == 1 else
                          "Open the documents to see why.", link=link, link_label=label, key="failed", ackable=True,
                          earlier=failed_earlier, ids=[str(j.id) for j in failed_jobs]))
    refunds, ai_earlier = fresh("ai_unavailable", [
        j for j in db.scalars(select(Job).where(job_scope, Job.status == "completed", Job.finished_at > now - RECENT))
        if (j.cost or {}).get("ai_refunded") and not (j.cost or {}).get("ai_unused")])
    if refunds:
        tiers = sorted({j.ai_tier or "?" for j in refunds})
        link, label = _documents_link(db, refunds, everyone, days=7)
        items.append(Item("warn", "ai_unavailable", f"The AI wasn't available for {len(refunds)} "
                          f"document{'s' if len(refunds) > 1 else ''} in the last 7 days",
                          f"Every AI call failed, so they used layout rules only (AI pages given back). "
                          f"AI levels: {', '.join(tiers)}.", count=len(refunds),
                          fix="Check the AI gateway and its models (Administration → AI gateway).", link=link,
                          link_label=label, key="ai_unavailable", ackable=True, earlier=ai_earlier,
                          ids=[str(j.id) for j in refunds]))
    for i in items:
        i.key = i.key or ":".join(x for x in (i.kind, i.group_id, i.service_key) if x)
        if i.ackable and scope is None:
            i.ackable = False  # a list of several groups: acknowledge it in each group's own
    order = {"bad": 0, "warn": 1, "info": 2}
    items.sort(key=lambda i: (order[i.severity], i.group_name or "", i.title))
    return items


def acknowledge(db: Session, group_ids: list[uuid.UUID] | None, key: str, by: str):
    """Acknowledge an item in this list (#538): it's hidden, for everyone who sees the list, until something new joins
    what it's about. Returns the row; None if there's no such item, or it can't be acknowledged."""
    from portal.models import AttentionAck
    scope = scope_of(group_ids)
    item = next((i for i in attention(db, group_ids, with_acknowledged=True) if i.key == key), None)
    if scope is None or item is None or not item.ackable:
        return None
    # Alerts acknowledged over a week ago are long off the list: their rows go
    db.query(AttentionAck).filter(AttentionAck.scope == scope, AttentionAck.key.like("security:%"),
                                  AttentionAck.acked_at < _now() - RECENT).delete(synchronize_session=False)
    row = db.scalar(select(AttentionAck).where(AttentionAck.scope == scope, AttentionAck.key == key))
    if row is None:
        row = AttentionAck(scope=scope, key=key, group_id=None if group_ids is None else group_ids[0])
        db.add(row)
    row.ids, row.title, row.detail, row.acked_by, row.acked_at = item.ids, item.title[:300], item.detail, by, _now()
    db.flush()
    return row


def unacknowledge(db: Session, group_ids: list[uuid.UUID] | None, key: str):
    """Show an acknowledged item again, in full. Returns the row removed, or None."""
    from portal.models import AttentionAck
    row = db.scalar(select(AttentionAck).where(AttentionAck.scope == scope_of(group_ids), AttentionAck.key == key))
    if row is not None:
        db.delete(row)
        db.flush()
    return row


def acknowledged(db: Session, group_ids: list[uuid.UUID] | None) -> list[dict]:
    """The list's acknowledged items, newest first: what each said, who acknowledged it and when, and how many of
    the things it was about are still there (0: all of them dealt with or out of its window)."""
    from portal.models import AttentionAck
    scope = scope_of(group_ids)
    if scope is None:
        return []
    current = {i.key: set(i.ids) for i in attention(db, group_ids, with_acknowledged=True) if i.ackable}
    rows = db.scalars(select(AttentionAck).where(AttentionAck.scope == scope).order_by(AttentionAck.acked_at.desc()))
    return [{"key": r.key, "title": r.title, "detail": r.detail, "acked_by": r.acked_by, "acked_at": r.acked_at,
             "count": len(r.ids or []), "still": len(current.get(r.key, set()) & set(r.ids or []))} for r in rows]


def acked(db: Session, request, p, group_ids, row, key: str) -> list[dict]:
    """For the endpoints, super admins' and a group's: the audit entry, and the list as it is now."""
    from portal import audit
    from portal.errors import ApiError
    if row is None:
        raise ApiError(409, "not_acknowledgeable", "That item isn't on the list now, or it can't be acknowledged: "
                                                   "it goes when it's fixed.")
    audit.record(db, request, "attention.acknowledged", actor=p.user, target_type="attention", target_id=key,
                 group_id=row.group_id, details={"key": key, "title": row.title, "count": len(row.ids or [])})
    db.commit()
    return [i.to_dict() for i in attention(db, group_ids)]


def unacked(db: Session, request, p, group_ids, row, key: str) -> list[dict]:
    """The audit entry for showing an item again, and the list as it is now."""
    from portal import audit
    if row is not None:
        audit.record(db, request, "attention.unacknowledged", actor=p.user, target_type="attention", target_id=key,
                     group_id=row.group_id, details={"key": key, "title": row.title,
                                                     "acknowledged_by": row.acked_by})
        db.commit()
    return [i.to_dict() for i in attention(db, group_ids)]
