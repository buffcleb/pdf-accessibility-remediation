"""Security alerts (#456): patterns in the audit log that someone should look at, emailed to every super admin, shown
on Attention for a day, and recorded in the audit log as `security.alert` (so they reach the campus SIEM with
everything else).

Counted over the last `security_window_minutes` (Settings):
- failed_codes: `security_failed_codes` wrong sign-in codes for one address;
- ip_addresses: sign-in attempts for `security_ip_addresses` different addresses from one IP;
- ip_refused: `security_ip_refused` code requests from one IP refused by the rate limit or the bot check.

Counted over the last hour:
- unknown_addresses: `security_unknown_per_hour` codes asked for addresses the service doesn't know (probing);
- bulk_downloads: one person downloading other people's documents `security_downloads_per_hour` times.

Each time it happens:
- refresh_reuse: a session's refresh token used twice (a stolen cookie; the session is already ended);
- super_admin: someone made a super admin;
- external_on: a group given an AI level or Adobe (documents leave the service), or its local-only turned off;
- retention_shortened: documents or records kept for less time than before;
- malware_blocked: an upload refused by the malware scanner (#455).

A counted alert fires once per address, IP or person per window, never at every check; one that happens each time
fires once per event. The portal worker checks about once a minute, with the service's own alerts (portal/alerts.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from portal import settings_store
from portal.alerts import mail_super_admins
from portal.config import get_portal_settings
from portal.models import AuditEvent, Service, WorkerBeat

log = logging.getLogger(__name__)

ACTION = "security.alert"
CHECKED = "security-alerts"  # WorkerBeat: when the events were last read, for the ones that fire each time
SHOWN = timedelta(hours=24)  # on Attention
TITLES = {
    "failed_codes": "Repeated wrong sign-in codes for one address",
    "ip_addresses": "Sign-in attempts for many addresses from one IP",
    "ip_refused": "Many refused sign-in requests from one IP",
    "unknown_addresses": "Sign-in codes asked for many unknown addresses",
    "bulk_downloads": "One person downloading many of other people's documents",
    "refresh_reuse": "A stolen session cookie may have been used",
    "super_admin": "A new super admin",
    "external_on": "Documents from a group may now leave the service",
    "retention_shortened": "Documents or records are kept for less time",
    "malware_blocked": "An upload was refused: the malware scanner found something in it",
}
FIXES = {
    "failed_codes": "Someone may be guessing codes. The code stops working after a few tries; if it goes on, or the "
                    "person didn't try to sign in, disable the account (Administration > People) and look at the "
                    "Sign-ins report.",
    "ip_addresses": "One address trying many accounts is usually an attack. Look at the Sign-ins report for the IP; "
                    "block it at the firewall or Cloudflare if it goes on.",
    "ip_refused": "The rate limit and the bot check are refusing it already. Block the IP at the firewall or "
                  "Cloudflare if it goes on.",
    "unknown_addresses": "Someone may be finding out which addresses have accounts. Look at the Sign-ins report.",
    "bulk_downloads": "Check with the person, or their group's admin, that it was expected (the audit log says whose "
                      "documents, and the reason given). If not, disable the account.",
    "refresh_reuse": "The session has been ended. Ask the person whether they signed in somewhere they shouldn't "
                     "have, and sign them out everywhere (Administration > People).",
    "super_admin": "Check it was intended. A super admin can see and change everything.",
    "external_on": "Check it was intended and that the group may send its documents to the provider.",
    "retention_shortened": "Check it was intended: documents and records older than this are deleted at the next "
                           "clean-up.",
    "malware_blocked": "The file wasn't kept. Ask the person where it came from: their computer may be infected, or "
                       "it may be a false alarm (the signature's name helps). Look at their other uploads in the audit "
                       "log.",
}
RUNBOOK = "docs/operations/incident-response.md"


@dataclass
class Found:
    rule: str
    subject: str  # what it fired for: an address, an IP, a person, or the event that caused it
    detail: str
    search: str  # for the audit log link
    count: int = 1
    group_id: object = None
    ip: str | None = None
    extra: dict = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _span(first: datetime, last: datetime) -> str:
    end = f"{last:%H:%M}" if last.date() == first.date() else f"{last:%Y-%m-%d %H:%M}"
    return f"{first:%Y-%m-%d %H:%M} to {end}"


def _counted(db: Session, rs, now: datetime) -> list[Found]:
    out: list[Found] = []
    window = now - timedelta(minutes=rs.security_window_minutes)
    hour = now - timedelta(hours=1)
    mins = rs.security_window_minutes
    e = AuditEvent
    ip = func.host(e.ip)

    rows = db.execute(select(e.actor_email, func.count(), func.min(e.created_at), func.max(e.created_at),
                             func.count(func.distinct(ip)), func.max(ip))
                      .where(e.action == "login", e.outcome == "invalid_code", e.created_at >= window,
                             e.actor_email.is_not(None))
                      .group_by(e.actor_email).having(func.count() >= rs.security_failed_codes))
    for email, n, first, last, ips, one in rows:
        where = f"from {one}" if ips == 1 else f"from {ips} IP addresses"
        out.append(Found("failed_codes", email, f"{n} wrong sign-in codes for {email} in {mins} minutes, {where}, "
                                                f"{_span(first, last)} UTC.", email, n, ip=one if ips == 1 else None))

    attempts = e.action.in_(("login.code_requested", "login"))
    rows = db.execute(select(ip, func.count(func.distinct(func.lower(e.actor_email))), func.count(),
                             func.min(e.created_at), func.max(e.created_at))
                      .where(attempts, e.ip.is_not(None), e.created_at >= window)
                      .group_by(ip).having(func.count(func.distinct(func.lower(e.actor_email)))
                                           >= rs.security_ip_addresses))
    for addr, emails, n, first, last in rows:
        out.append(Found("ip_addresses", addr, f"Sign-in attempts for {emails} different addresses from {addr} in "
                                               f"{mins} minutes ({n} attempts), {_span(first, last)} UTC.",
                         addr, emails, ip=addr))

    rows = db.execute(select(ip, func.count(), func.min(e.created_at), func.max(e.created_at))
                      .where(e.action == "login.code_requested", e.outcome.in_(("rate_limited", "bot_check_failed")),
                             e.ip.is_not(None), e.created_at >= window)
                      .group_by(ip).having(func.count() >= rs.security_ip_refused))
    for addr, n, first, last in rows:
        out.append(Found("ip_refused", addr, f"{n} sign-in code requests from {addr} refused by the rate limit or the "
                                             f"bot check in {mins} minutes, {_span(first, last)} UTC.",
                         addr, n, ip=addr))

    n, ips, first, last, one = db.execute(
        select(func.count(), func.count(func.distinct(ip)), func.min(e.created_at), func.max(e.created_at),
               func.max(ip))
        .where(e.action == "login.code_requested", e.outcome == "not_provisioned", e.created_at >= hour)).one()
    if n >= rs.security_unknown_per_hour:
        where = f"all from {one}" if ips == 1 else f"from {ips} IP addresses"
        out.append(Found("unknown_addresses", "service", f"{n} sign-in codes asked for addresses the service doesn't "
                                                         f"know in the last hour, {where}, {_span(first, last)} UTC.",
                         "not_provisioned", n, ip=one if ips == 1 else None))

    owner = e.details["owner"].astext
    rows = db.execute(select(e.actor_id, func.max(e.actor_email), func.count(), func.count(func.distinct(owner)),
                             func.min(e.created_at), func.max(e.created_at))
                      .where(e.action == "file.accessed", e.actor_id.is_not(None), e.created_at >= hour)
                      .group_by(e.actor_id).having(func.count() >= rs.security_downloads_per_hour))
    for actor_id, email, n, owners, first, last in rows:
        out.append(Found("bulk_downloads", str(actor_id), f"{email} downloaded {n} files from other people's "
                                                          f"documents ({owners} people's) in the last hour, "
                                                          f"{_span(first, last)} UTC.", email, n))
    return out


def _changed(details: dict, key: str) -> tuple | None:
    pair = (details or {}).get(key)
    return tuple(pair) if isinstance(pair, list) and len(pair) == 2 else None


def _each_time(db: Session, since: datetime) -> list[Found]:
    out: list[Found] = []
    e = AuditEvent
    rows = db.scalars(select(e).where(e.created_at >= since, e.action.in_(
        ("session.revoked", "role.granted", "group.service_updated", "group.updated", "settings.updated",
         "upload.blocked")))
        .order_by(e.id))
    external = {s.key for s in db.scalars(select(Service).where(Service.external.is_(True)))}
    for ev in rows:
        d, who, when = ev.details or {}, ev.actor_email or "someone", f"{ev.created_at:%Y-%m-%d %H:%M} UTC"
        subject = f"event:{ev.id}"
        if ev.action == "session.revoked" and ev.outcome == "refresh_reuse":
            out.append(Found("refresh_reuse", subject, f"{who}'s session refresh token was used a second time, "
                                                       f"{when}" + (f", from {ev.ip}" if ev.ip else "") +
                             ". The session was ended.", who, group_id=ev.group_id,
                             ip=str(ev.ip) if ev.ip else None))
        elif ev.action == "role.granted" and d.get("role") == "super_admin":
            out.append(Found("super_admin", subject, f"{who} made {d.get('email') or 'someone'} a super admin, {when}.",
                             d.get("email") or who, group_id=ev.group_id))
        elif ev.action == "group.service_updated" and d.get("service") in external \
                and _changed(d, "enabled") == (False, True):
            out.append(Found("external_on", subject, f"{who} turned on {d['service']} for a group, {when}: its "
                                                     "documents can now go to that provider.",
                             d["service"], group_id=ev.group_id))
        elif ev.action == "group.updated" and _changed(d, "local_only") == (True, False):
            out.append(Found("external_on", subject, f"{who} turned local-only off for a group, {when}: its documents "
                                                     "can now go to outside providers.", "local_only",
                             group_id=ev.group_id))
        elif ev.action == "upload.blocked":
            out.append(Found("malware_blocked", subject, f"{who}'s upload {d.get('filename') or ''!r} ({d.get('for')}) "
                                                         f"was refused, {when}: {d.get('signature')}.", who,
                             group_id=ev.group_id, ip=str(ev.ip) if ev.ip else None))
        elif ev.action == "settings.updated":
            shorter = []
            for k in ("file_retention_days", "record_retention_days"):
                old, new = _changed(d, k) or (None, None)
                if old is not None and new is not None and new < old:
                    shorter.append(f"{k.replace('_', ' ')} from {old} to {new}")
            if shorter:
                out.append(Found("retention_shortened", subject, f"{who} changed {' and '.join(shorter)}, {when}.",
                                 "retention_days"))
    return out


def _already(db: Session, rule: str, subject: str, since: datetime) -> bool:
    """Whether this alert was already raised since then."""
    return db.scalar(select(AuditEvent.id).where(
        AuditEvent.action == ACTION, AuditEvent.created_at >= since, AuditEvent.details["rule"].astext == rule,
        AuditEvent.details["subject"].astext == subject).limit(1)) is not None


def link(search: str) -> str:
    """The audit log, the last week, searched for what the alert is about."""
    return f"{get_portal_settings().app_url.rstrip('/')}/admin/reports?report=audit&range=7d&q={quote(search)}"


def evaluate(db: Session, now: datetime | None = None) -> list[Found]:
    """Look for the patterns, record and email the new ones. The caller commits. Returns what fired."""
    rs = settings_store.load(db)
    now = now or _now()
    if not rs.security_alerts:
        return []
    checked = db.get(WorkerBeat, CHECKED)
    # Events since the last look (a little before, in case one was written late), at most a day back
    since = max(now - SHOWN, (checked.at if checked else now - timedelta(minutes=rs.security_window_minutes))
                - timedelta(minutes=2))
    hourly = {"unknown_addresses", "bulk_downloads"}
    fired: list[Found] = []
    for f in _counted(db, rs, now):
        window = timedelta(hours=1) if f.rule in hourly else timedelta(minutes=rs.security_window_minutes)
        if not _already(db, f.rule, f.subject, now - window):
            fired.append(f)
    fired += [f for f in _each_time(db, since) if not _already(db, f.rule, f.subject, now - 2 * SHOWN)]
    for f in fired:  # at the check's time, which the next checks compare with
        db.execute(insert(AuditEvent.__table__).inline().values(
            created_at=now, action=ACTION, outcome="alert", target_type="security", target_id=f.rule,
            group_id=f.group_id, details={"rule": f.rule, "subject": f.subject, "title": TITLES[f.rule],
                                          "detail": f.detail, "count": f.count, "ip": f.ip,
                                          "search": f.search, **f.extra}))  # the search: Attention's link (#538)
    if checked is None:
        db.add(WorkerBeat(name=CHECKED, at=now))
    else:
        checked.at = now
    if fired:
        subject = (f"Security alert: {TITLES[fired[0].rule]}" if len(fired) == 1
                   else f"Security alerts: {len(fired)} to look at")
        text = "\n\n".join(f"{TITLES[f.rule]}\n{f.detail}\nWhat to do: {FIXES[f.rule]}\nIn the audit log: "
                           f"{link(f.search)}" for f in fired)
        text += (f"\n\nIf it looks like an incident: {RUNBOOK} (contain first, then keep the evidence). These are "
                 "also on Administration > Attention for a day.")
        mail_super_admins(db, "security", subject, text)
        log.warning("security alerts: %s", ", ".join(f"{f.rule} ({f.subject})" for f in fired))
    return fired


def recent(db: Session, now: datetime | None = None) -> list[AuditEvent]:
    """The security alerts of the last day, newest first, for Attention."""
    now = now or _now()
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == ACTION, AuditEvent.created_at >= now - SHOWN)
                           .order_by(AuditEvent.id.desc()).limit(20)))
