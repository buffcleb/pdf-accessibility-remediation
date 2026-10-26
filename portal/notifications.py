"""How often people get notification emails, by kind:

  documents  a document is done, couldn't be processed, or was canceled by support
  help       news on their help requests (replies, status changes, reminders); for support, new requests and replies
  admin      notices for admins (someone waiting for approval)
  alerts     for service admins (#564): the service's own alerts, which super admins always get at once

Each person chooses "each" (as it happens), "daily" (one summary a day) or "off", in Settings; without a choice,
the service default applies (Settings → Email). Sign-in codes, the email telling someone they were approved or
declined, and scheduled reports (their own settings) are never affected.

Held notifications wait in the outbox ("held") and go out together each day at the report hour, in the service
time zone (Settings → Email), as one email per person.

Seen in the portal: an email about a document or help request waits a couple of minutes; if the person had that
document (or their documents list) or that request open in the portal when the news came, it isn't sent (status
"seen"), unless they asked for emails even about what they've seen (a choice per kind: help requests are skipped by
default, documents still emailed). The app reports what's open every half minute
while the page is in view.
"""
from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from portal import audit, settings_store
from portal.config import get_portal_settings
from portal.errors import ApiError
from portal.models import MailOutbox, User, UserPresence

log = logging.getLogger(__name__)

CHOICES = ("each", "daily", "off")
CATEGORIES = {
    "documents": ("Your documents", "When a document is done, couldn't be processed, or was canceled by support."),
    "help": ("Help requests", "Replies and changes on help requests, and reminders when one is waiting for you."),
    "admin": ("Admin notices", "When someone in a group you look after is waiting for approval."),
    "alerts": ("Service alerts", "When something is wrong with the service, such as work that's stuck or email that "
                                 "isn't going out, and when it clears."),
}
# Outbox kinds and the setting that governs them; any other kind is always sent at once
KIND_CATEGORY = {"job": "documents", "help": "help", "approval": "admin", "staff_alert": "alerts"}
# Without a choice, and with no service default of its own (Settings → Email): as it happens
NO_DEFAULT = "each"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def applies(user: User) -> list[str]:
    kinds = ["documents", "help"]
    if user.across_groups or user.admin_group_ids():
        kinds.append("admin")
    if user.is_service_admin and not user.is_super_admin:
        kinds.append("alerts")
    return kinds


def choice(db: Session, user: User | None, category: str) -> str:
    if user is not None and (user.notify_prefs or {}).get(category) in CHOICES:
        return user.notify_prefs[category]
    return getattr(settings_store.load(db), f"notify_default_{category}", NO_DEFAULT)


def choices(db: Session, user: User) -> dict[str, str]:
    return {c: choice(db, user, c) for c in applies(user)}


def set_choices(db: Session, request: Request | None, user: User, wanted: dict[str, str]) -> None:
    prefs = dict(user.notify_prefs or {})
    changed = {}
    for category, value in wanted.items():
        if category not in applies(user) or value not in CHOICES:
            raise ApiError(422, "bad_choice", "Unknown kind of email or choice.")
        old = choice(db, user, category)
        prefs[category] = value
        if old != value:
            changed[category] = [old, value]
    user.notify_prefs = prefs
    if changed:
        audit.record(db, request, "notifications.changed", actor=user, target_type="user", target_id=user.id,
                     group_id=user.group_id, details=changed)


SEEN_DELAY = timedelta(minutes=2)  # time for the app to report what's open before an email goes
SEEN_SLACK = timedelta(seconds=45)  # the app reports every 30 s: open a little before the news counts too
PRESENCE_KEEP = timedelta(days=1)


# Email even about what they saw in the portal? Per kind: help-request chat is skipped by default (back-and-forth
# would flood the inbox); document emails still come by default, since people keep them as a record.
SEEN_KINDS = {"documents": True, "help": False}


def even_if_seen(user: User | None, category: str) -> bool:
    prefs = (user.notify_prefs or {}) if user is not None else {}
    value = (prefs.get("even_if_seen") or {}).get(category) if isinstance(prefs.get("even_if_seen"), dict) else None
    return SEEN_KINDS.get(category, True) if value is None else bool(value)


def set_even_if_seen(db: Session, request: Request | None, user: User, category: str, value: bool) -> None:
    if category not in SEEN_KINDS:
        raise ApiError(422, "bad_choice", "Unknown kind of email.")
    old = even_if_seen(user, category)
    if old == value:
        return
    prefs = dict(user.notify_prefs or {})
    current = prefs.get("even_if_seen") if isinstance(prefs.get("even_if_seen"), dict) else {}
    prefs["even_if_seen"] = {**current, category: value}
    user.notify_prefs = prefs
    audit.record(db, request, "notifications.changed", actor=user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={f"even_if_seen_{category}": [old, value]})


def mark_seen(db: Session, user: User, items: list[str]) -> None:
    """The app: these are open and in view now."""
    now = _now()
    for item in dict.fromkeys(items):
        db.execute(insert(UserPresence).values(user_id=user.id, item=item, seen_at=now).on_conflict_do_update(
            index_elements=["user_id", "item"], set_={"seen_at": now}))


def seen(db: Session, to: str, about: str | None, since: datetime) -> bool:
    """Whether the person had what this email is about open since just before it happened (and wants that to
    count)."""
    if not about:
        return False
    user = db.scalar(select(User).where(func.lower(User.email) == to.lower()))
    category = "documents" if about.startswith("job:") else "help"
    if user is None or even_if_seen(user, category):
        return False
    items = [about] + (["jobs"] if about.startswith("job:") else [])
    return bool(db.scalar(select(func.count()).select_from(UserPresence).where(
        UserPresence.user_id == user.id, UserPresence.item.in_(items), UserPresence.seen_at >= since - SEEN_SLACK)))


def purge_presence(db: Session) -> int:
    return db.execute(delete(UserPresence).where(UserPresence.seen_at < _now() - PRESENCE_KEEP)).rowcount


def status_for(db: Session, to: str, kind: str) -> str | None:
    """How a new email should go: "queued" (now), "held" (in the daily summary), or None (not sent)."""
    category = KIND_CATEGORY.get(kind)
    if category is None:
        return "queued"
    user = db.scalar(select(User).where(func.lower(User.email) == to.lower()))
    c = choice(db, user, category)
    return None if c == "off" else "held" if c == "daily" else "queued"


# --- the daily summary -----------------------------------------------------------------------------

def _summary_time(now: datetime, zone: ZoneInfo, hour: int) -> datetime:
    """Today's summary time, in UTC (the moment the latest summary was due)."""
    local = now.astimezone(zone)
    at = datetime.combine(local.date(), time(hour), zone)
    if local < at:
        at = datetime.combine(local.date() - timedelta(days=1), time(hour), zone)
    return at.astimezone(timezone.utc)


def send_digests(db: Session, now: datetime | None = None) -> int:
    """Worker: once the summary time has passed, combine each person's held notifications from before it into one
    email. Returns how many summaries were queued."""
    rs = settings_store.load(db)
    now = now or _now()
    due = _summary_time(now, ZoneInfo(rs.report_time_zone), rs.report_send_hour)
    held = db.scalars(select(MailOutbox).where(MailOutbox.status == "held", MailOutbox.created_at < due)
                      .order_by(MailOutbox.created_at).with_for_update(skip_locked=True)).all()
    by_person: dict[str, list[MailOutbox]] = defaultdict(list)
    for row in held:
        if seen(db, row.to_addr, row.about, row.created_at):  # they saw it in the portal: leave it out
            row.status, row.finished_at = "seen", now
            continue
        by_person[row.to_addr].append(row)
    for to, rows in by_person.items():
        summary = _compose(to, rows, rs)
        db.add(summary)
        for row in rows:
            row.status, row.finished_at = "combined", now
    db.commit()
    return len(by_person)


def _compose(to: str, rows: list[MailOutbox], rs) -> MailOutbox:
    e = html.escape
    n = len(rows)
    subject = f"{rs.service_name}: {n} notification{'s' if n > 1 else ''} from the last day"
    by_cat: dict[str, list[MailOutbox]] = defaultdict(list)
    for r in rows:
        by_cat[KIND_CATEGORY.get(r.kind, "documents")].append(r)
    settings_url = get_portal_settings().app_url.rstrip("/") + "/settings"
    text = [f"Your daily summary: {n} notification{'s' if n > 1 else ''}.", ""]
    accent = rs.accent_color or "#1f4e8c"
    body = [f'<div style="border-bottom:3px solid {e(accent)};padding:0 0 10px;margin:0 0 16px">'
            f'<div style="font-size:1.25em;font-weight:bold">{e(rs.service_name)}</div>'
            f'<div style="color:#444">Your daily summary: {n} notification{"s" if n > 1 else ""}.</div></div>']
    for cat in CATEGORIES:
        items = by_cat.get(cat)
        if not items:
            continue
        label = CATEGORIES[cat][0]
        text += [label.upper(), "=" * len(label), ""]
        body.append(f'<h1 style="font-size:1.25em;margin:24px 0 8px;color:{e(accent)}">{e(label)}</h1>')
        for r in items:
            text += [r.subject, "-" * min(len(r.subject), 70), r.body.strip(), ""]
            body.append(f'<h2 style="font-size:1.05em;margin:16px 0 4px">{e(r.subject)}</h2>'
                        f'<div style="white-space:pre-wrap">{e(r.body.strip())}</div>')
    text += ["--", f"You chose a daily summary for these emails. Change it in Settings: {settings_url}"]
    body.append(f'<hr><p style="font-size:0.9em;color:#444">You chose a daily summary for these emails. '
                f'<a href="{e(settings_url)}">Change it in Settings</a>.</p>')
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + e(subject) + "</title></head>"
            '<body style="font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;line-height:1.45;max-width:680px">'
            + "".join(body) + "</body></html>")
    return MailOutbox(kind="digest", to_addr=to, subject=subject, body="\n".join(text) + "\n", html=page,
                      from_name=rows[-1].from_name, status="queued", next_try_at=_now())
