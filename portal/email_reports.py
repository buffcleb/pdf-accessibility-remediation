"""Scheduled email reports: a summary of the last month (or week, or day) in people's inboxes.

Three reports, each for those it applies to *when it's sent*:
  my_month         everyone: their documents, pages used and left, and anything waiting for them
  group_summary    group admins: each group's pools, most active people, pass rate, help requests, approvals
  service_summary  super admins: usage by group and service, AI and Adobe cost, daily limits, quality, help queue

Everyone gets theirs monthly unless they change it (Settings → Email reports in the app, or the unsubscribe link in
each email). A person's reports with the same frequency go out together, as one email, once per period: the
send is recorded, so a restart never sends it twice, and a missed send catches up once (within a few days).
Schedules follow one service time zone (Settings → Email): daily reports cover the day before, weekly ones the
week before (Monday to Sunday, sent on Monday), monthly ones the month before (sent on its first working day).
"""
from __future__ import annotations

import base64
import html
import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import Float, cast, func, select
from sqlalchemy import true as sa_true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import audit, outbox, services as svc, settings_store
from portal.attention import _low
from portal.config import get_portal_settings
from portal.errors import ApiError
from portal.mail import Mail
from portal.models import (
    REPORT_FREQUENCIES, Group, GroupService, HelpRequest, Job, MailOutbox, QuotaLedger, ReportSend,
    ReportSubscription, User,
)
from portal.security.tokens import constant_time_equal, keyed_hash

log = logging.getLogger(__name__)

KINDS = {
    "my_month": ("Your documents", "Your documents, pages used and left, and anything waiting for you."),
    "group_summary": ("Group summary", "For each group you look after: pages used and left, the most active people, "
                                       "how many documents pass, help requests and people waiting for approval."),
    "service_summary": ("Service summary", "Across every group: usage, AI and Adobe cost, daily limits, quality and "
                                           "the help queue."),
}
DEFAULT_FREQUENCY = "monthly"
# A missed send catches up once, but not long after: switching this on mid-month doesn't email last month
CATCH_UP = {"daily": timedelta(hours=20), "weekly": timedelta(days=3), "monthly": timedelta(days=7)}
TOP = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- who gets what -----------------------------------------------------------------------------------

def eligible(user: User) -> list[str]:
    """The reports that apply to someone now, by their roles."""
    if user.status != "active":
        return []
    kinds = []
    if user.group_id is not None and user.approval == "approved":
        kinds.append("my_month")
    roles = {r.role for r in user.roles}
    if "group_admin" in roles:
        kinds.append("group_summary")
    if "super_admin" in roles:
        kinds.append("service_summary")
    return kinds


def choices(db: Session, user: User) -> dict[str, str]:
    """{kind: frequency} for each report that applies to them."""
    chosen = {s.kind: s.frequency for s in db.scalars(select(ReportSubscription).where(ReportSubscription.user_id == user.id))}
    return {k: chosen.get(k, DEFAULT_FREQUENCY) for k in eligible(user)}


def set_choice(db: Session, request: Request | None, user: User, kind: str, frequency: str, how: str) -> None:
    if kind not in KINDS or frequency not in REPORT_FREQUENCIES:
        raise ApiError(422, "bad_choice", "Unknown report or frequency.")
    row = db.get(ReportSubscription, (user.id, kind))
    old = row.frequency if row else DEFAULT_FREQUENCY
    if old == frequency:
        return
    if row is None:
        db.add(ReportSubscription(user_id=user.id, kind=kind, frequency=frequency))
    else:
        row.frequency, row.updated_at = frequency, _now()
    audit.record(db, request, "email_report.changed", actor=user, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"report": kind, "from": old, "to": frequency, "how": how})


# --- unsubscribe links ------------------------------------------------------------------------------

def unsubscribe_token(user: User, kind: str) -> str:
    payload = f"{user.id}.{kind}"
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{raw}.{keyed_hash(payload, 'unsubscribe')[:32]}"


def read_token(db: Session, token: str) -> tuple[User, str]:
    """The person and report an unsubscribe link is for; it needs no sign-in, only the signature."""
    try:
        raw, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        user_id, kind = payload.split(".", 1)
        # A built-in report, or "view:<id>" for a scheduled report view
        ok = constant_time_equal(sig, keyed_hash(payload, "unsubscribe")[:32]) and (
            kind in KINDS or (kind.startswith("view:") and bool(uuid.UUID(kind[5:]))))
        user = db.get(User, uuid.UUID(user_id)) if ok else None
    except (ValueError, UnicodeDecodeError):
        user = None
    if user is None:
        raise ApiError(404, "bad_link", "This link isn't valid. You can change your email reports after signing in.")
    return user, kind


# --- when ----------------------------------------------------------------------------------------------

def _first_working_day(d: date) -> date:
    d = d.replace(day=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _month_before(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def latest_period(frequency: str, now: datetime, zone: ZoneInfo, hour: int) -> tuple[datetime, datetime, datetime]:
    """(start, end, due) of the most recent period whose report is due by `now`; start/end in UTC."""
    local = now.astimezone(zone)
    at = lambda d: datetime.combine(d, time(hour), zone)  # noqa: E731
    today = local.date()
    if frequency == "daily":
        end = today if local >= at(today) else today - timedelta(days=1)
        start, due = end - timedelta(days=1), at(end)
    elif frequency == "weekly":
        end = today - timedelta(days=today.weekday())
        if local < at(end):
            end -= timedelta(days=7)
        start, due = end - timedelta(days=7), at(end)
    else:
        send = _first_working_day(today)
        if local < at(send):
            send = _first_working_day(_month_before(today))
        end = send.replace(day=1)
        start, due = _month_before(end), at(send)
    utc = lambda d: datetime.combine(d, time(0), zone).astimezone(timezone.utc)  # noqa: E731
    return utc(start), utc(end), due.astimezone(timezone.utc)


def period_label(frequency: str, start: datetime, end: datetime, zone: ZoneInfo) -> str:
    s, e = start.astimezone(zone).date(), (end.astimezone(zone) - timedelta(days=1)).date()
    if frequency == "monthly":
        return s.strftime("%B %Y")
    if frequency == "weekly":
        return f"the week of {s.strftime('%-d %B %Y')}" if s.month == e.month else \
            f"{s.strftime('%-d %B')} to {e.strftime('%-d %B %Y')}"
    return s.strftime("%A %-d %B %Y")


# --- what ----------------------------------------------------------------------------------------------

@dataclass
class Table:
    caption: str
    header: list[str]
    rows: list[list]
    numeric: set[int] = field(default_factory=set)  # columns aligned right


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    link: tuple[str, str] | None = None  # (text, url)


def _url(path: str) -> str:
    return get_portal_settings().app_url.rstrip("/") + path


def _n(v) -> str:
    return f"{int(v or 0):,}"


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "–"


def _finished(where, start, end):
    return [*where, Job.finished_at >= start, Job.finished_at < end]


def _quality(db: Session, where: list) -> tuple[int, int]:
    """(documents finished, of which pass PDF/UA: PDF/UA-1, or PDF/UA-2 for a PDF 2.0 document)."""
    total = db.scalar(select(func.count()).select_from(Job).where(*where, Job.status == "completed")) or 0
    passed = db.scalar(select(func.count()).select_from(Job).where(
        *where, Job.status == "completed", Job.result_summary["compliant_after"].as_boolean().is_(True))) or 0
    return total, passed


def _used(db: Session, where: list, start, end, by) -> list:
    return db.execute(select(*by, func.sum(QuotaLedger.pages)).where(
        *where, QuotaLedger.kind == "commit", QuotaLedger.created_at >= start, QuotaLedger.created_at < end)
        .group_by(*by)).all()


def my_month(db: Session, user: User, start: datetime, end: datetime) -> Section | None:
    total, passed = _quality(db, _finished([Job.user_id == user.id], start, end))
    failed = db.scalar(select(func.count()).select_from(Job).where(
        *_finished([Job.user_id == user.id], start, end), Job.status == "failed")) or 0
    used = dict(_used(db, [QuotaLedger.user_id == user.id], start, end, [QuotaLedger.service_key]))
    held = db.scalar(select(func.count()).select_from(Job).where(Job.user_id == user.id, Job.status == "held")) or 0
    replies = db.scalar(select(func.count()).select_from(HelpRequest).where(
        HelpRequest.user_id == user.id, HelpRequest.status == "waiting")) or 0
    if not (total or failed or used or held or replies):
        return None  # nothing happened and nothing's waiting: no email about it
    sec = Section("Your documents", link=("See your documents", _url("/")))
    sec.lines.append(f"Documents processed: {_n(total)}" + (f" ({_n(failed)} more couldn't be processed)" if failed else ""))
    if total:
        sec.lines.append(f"Pass the PDF/UA accessibility check: {_n(passed)} of {_n(total)} ({_pct(passed, total)})")
    rows = []
    for s in svc.all_services(db):
        if not svc.access(db, user, s.key).ok and not used.get(s.key):
            continue
        left = svc.remaining(user, s.key, s) if svc.access(db, user, s.key).ok else 0
        rows.append([s.label, _n(used.get(s.key)), "no limit" if left is None else _n(left)])
    if rows:
        sec.tables.append(Table("Pages", ["Processing option", "Used in this period", "Left now"], rows, {1, 2}))
    if held:
        sec.warnings.append(f"{_n(held)} document{'s are' if held > 1 else ' is'} waiting for you to confirm sending it.")
    if replies:
        sec.warnings.append(f"{_n(replies)} help request{'s are' if replies > 1 else ' is'} waiting for your reply.")
    return sec


def group_summary(db: Session, user: User, start: datetime, end: datetime) -> list[Section]:
    group_ids = [r.group_id for r in user.roles if r.role == "group_admin" and r.group_id]
    labels = {s.key: s for s in svc.all_services(db)}
    rs = settings_store.load(db)
    out = []
    for g in db.scalars(select(Group).where(Group.id.in_(group_ids)).order_by(Group.name)):
        sec = Section(g.name if len(group_ids) > 1 else f"Your group: {g.name}",
                      link=("Open the group", _url(f"/admin/groups/{g.id}")))
        used = dict(_used(db, [QuotaLedger.group_id == g.id, QuotaLedger.scope == "group"], start, end,
                          [QuotaLedger.service_key]))
        rows = []
        for gs in db.scalars(select(GroupService).where(GroupService.group_id == g.id, GroupService.enabled.is_(True))):
            s = labels.get(gs.service_key)
            if s is None:
                continue
            if not s.metered:
                rows.append([s.label, _n(used.get(s.key)), "no limit", "–"])
                continue
            left = gs.pool_pages - gs.used_pages - gs.reserved_pages
            rows.append([s.label, _n(used.get(s.key)), _n(gs.pool_pages), _n(max(0, left))])
            if left <= 0:
                sec.warnings.append(f"{s.label}: the group's pages have run out.")
            elif _low(left, gs.pool_pages, rs):
                sec.warnings.append(f"{s.label}: only {_n(left)} of {_n(gs.pool_pages)} pages left.")
        if rows:
            sec.tables.append(Table("Pages", ["Processing option", "Used in this period", "Pool", "Left now"], rows,
                                    {1, 2, 3}))
        people = db.execute(select(User.email, func.sum(QuotaLedger.pages).label("p")).join(
            User, User.id == QuotaLedger.user_id).where(
            QuotaLedger.group_id == g.id, QuotaLedger.kind == "commit", QuotaLedger.created_at >= start,
            QuotaLedger.created_at < end).group_by(User.email).order_by(func.sum(QuotaLedger.pages).desc()).limit(TOP)).all()
        if people:
            sec.tables.append(Table("Most active people", ["Person", "Pages"], [[e, _n(p)] for e, p in people], {1}))
        total, passed = _quality(db, _finished([Job.group_id == g.id], start, end))
        sec.lines.append(f"Documents processed: {_n(total)}" +
                         (f"; {_n(passed)} pass PDF/UA ({_pct(passed, total)})" if total else ""))
        open_help = db.scalar(select(func.count()).select_from(HelpRequest).where(
            HelpRequest.group_id == g.id, HelpRequest.status.in_(("open", "in_progress", "waiting")))) or 0
        sec.lines.append(f"Open help requests: {_n(open_help)}")
        waiting = db.scalar(select(func.count()).select_from(User).where(
            User.group_id == g.id, User.approval == "pending", User.status == "active")) or 0
        if waiting:
            sec.warnings.append(f"{_n(waiting)} {'people are' if waiting > 1 else 'person is'} waiting for approval.")
        out.append(sec)
    return out


def service_summary(db: Session, start: datetime, end: datetime) -> list[Section]:
    services = {s.key: s for s in svc.all_services(db)}
    groups = {g.id: g.name for g in db.scalars(select(Group))}
    usage = Section("Usage", link=("Open the reports", _url("/admin/reports?report=usage")))
    rows = sorted(([groups.get(gid, "No group"), services[k].label if k in services else k, p]
                   for gid, k, p in _used(db, [QuotaLedger.scope == "group"], start, end,
                                          [QuotaLedger.group_id, QuotaLedger.service_key])), key=lambda r: -r[2])
    usage.lines.append(f"Pages processed: {_n(sum(r[2] for r in rows))}")
    if rows:
        usage.tables.append(Table("Pages by group and processing option", ["Group", "Processing option", "Pages"],
                                  [[g, s, _n(p)] for g, s, p in rows[:20]], {2}))
        if len(rows) > 20:
            usage.lines.append(f"And {len(rows) - 20} more; the Usage report has them all.")

    cost = Section("Cost", link=("Open the cost report", _url("/admin/reports?report=cost")))
    done = _finished([], start, end)
    ai = db.scalar(select(func.coalesce(func.sum(cast(Job.cost["ai_usd"].as_string(), Float)), 0)).where(*done)) or 0
    adobe_keys = [k for k, s in services.items() if s.engine_route == "adobe"]
    adobe = db.scalar(select(func.coalesce(func.sum(Job.pages), 0)).where(
        *done, Job.status == "completed", Job.processing.in_(adobe_keys))) if adobe_keys else 0
    cost.lines.append(f"AI cost: ${float(ai):,.2f}")
    # Describe with AI in the tag manager, counted when used (#318)
    from portal.live_reports import _describe, _described_at
    uses, usd = db.execute(select(func.count(), func.coalesce(func.sum(cast(_describe.c.value["usd"].astext, Float)), 0))
                           .select_from(Job).join(_describe, sa_true())
                           .where(_described_at >= start, _described_at < end)).one()
    if uses:
        cost.lines.append(f"Describe with AI: {int(uses):,} use(s), ${float(usd):,.2f}")
    cost.lines.append(f"Pages sent to Adobe: {_n(adobe)}")
    # Days a service reached its daily limit (pages sent that day, by the day they were sent, UTC)
    capped = {k: s.daily_cap_pages for k, s in services.items() if s.daily_cap_pages}
    if capped:
        per_day: dict[tuple, int] = defaultdict(int)
        for submitted, charged, pages in db.execute(select(Job.submitted_at, Job.charged_services, Job.pages_charged).where(
                Job.submitted_at >= start, Job.submitted_at < end)):
            for k in charged or []:
                if k in capped:
                    per_day[(k, submitted.date())] += pages or 0
        hits = Counter(k for (k, _), p in per_day.items() if p >= capped[k])
        cost.lines.append("Days a daily limit was reached: " + (", ".join(
            f"{services[k].label} {n}" for k, n in hits.items()) if hits else "none"))

    quality = Section("Quality", link=("Open the quality report", _url("/admin/reports?report=quality")))
    total, passed = _quality(db, done)
    quality.lines.append(f"Documents processed: {_n(total)}" +
                         (f"; {_n(passed)} pass PDF/UA ({_pct(passed, total)})" if total else ""))
    clauses: Counter = Counter()
    names: dict[str, str] = {}
    for (summary,) in db.execute(select(Job.result_summary).where(*done, Job.status == "completed")):
        for f in (summary or {}).get("remaining_failures") or []:
            if f.get("clause"):
                clauses[f["clause"]] += 1
                names.setdefault(f["clause"], f.get("description") or "")
    if clauses:
        quality.tables.append(Table("Most common remaining failures", ["Rule", "What it checks", "Documents"],
                                    [[c, names[c], _n(n)] for c, n in clauses.most_common(TOP)], {2}))

    help_ = Section("Help requests", link=("Open help requests", _url("/admin/help")))
    active = list(db.scalars(select(HelpRequest).where(HelpRequest.status.in_(("open", "in_progress", "waiting")))))
    needs = [r for r in active if r.needs_staff]
    help_.lines.append(f"Open: {_n(len(active))}; waiting on support: {_n(len(needs))}")
    if needs:
        oldest = min(r.created_at for r in needs)
        help_.lines.append(f"The oldest waiting on support is {max(0, (_now() - oldest).days)} days old.")
    return [usage, cost, quality, help_]


def build(db: Session, user: User, kinds: list[str], start: datetime, end: datetime) -> list[tuple[str, Section]]:
    out: list[tuple[str, Section]] = []
    for kind in kinds:
        if kind == "my_month":
            sec = my_month(db, user, start, end)
            out += [(kind, sec)] if sec else []
        elif kind == "group_summary":
            out += [(kind, s) for s in group_summary(db, user, start, end)]
        elif kind == "service_summary":
            out += [(kind, s) for s in service_summary(db, start, end)]
    return out


# --- the email -------------------------------------------------------------------------------------

def _text_table(t: Table) -> list[str]:
    cells = [t.header] + [[str(c) for c in r] for r in t.rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(t.header))]
    line = lambda r: "  ".join((c.rjust if i in t.numeric else c.ljust)(widths[i]) for i, c in enumerate(r)).rstrip()  # noqa: E731
    return [t.caption, line(t.header), *[line(r) for r in cells[1:]]]


def _html_table(t: Table) -> str:
    e = html.escape
    cell = "padding:6px 10px;border-bottom:1px solid #d9dde3;"

    def style(i: int, head: bool = False) -> str:
        return (f' style="{cell}text-align:{"right" if i in t.numeric else "left"};'
                f'{"background:#f2f4f7;font-weight:bold;" if head else "font-weight:normal;"}"')
    head = "".join(f'<th scope="col"{style(i, True)}>{e(h)}</th>' for i, h in enumerate(t.header))
    body = "".join("<tr>" + "".join(
        (f'<th scope="row"{style(i)}>{e(str(c))}</th>' if i == 0 else f"<td{style(i)}>{e(str(c))}</td>")
        for i, c in enumerate(r)) + "</tr>" for r in t.rows)
    return (f'<table style="border-collapse:collapse;margin:8px 0 18px;min-width:60%"><caption style="text-align:left;'
            f'font-weight:bold;padding:4px 0 6px">{e(t.caption)}</caption><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table>")


def compose(db: Session, user: User, frequency: str, start: datetime, end: datetime,
            parts: list[tuple[str, Section]]) -> Mail:
    rs = settings_store.load(db)
    zone = ZoneInfo(rs.report_time_zone)
    label = period_label(frequency, start, end, zone)
    kinds = list(dict.fromkeys(k for k, _ in parts))
    subject = f"{rs.service_name}: your summary for {label}"
    intro = f"Your {frequency} summary for {label}."
    e = html.escape
    accent = rs.accent_color or "#1f4e8c"
    text, body = [intro, ""], [
        f'<div style="border-bottom:3px solid {e(accent)};padding:0 0 10px;margin:0 0 16px">'
        f'<div style="font-size:1.25em;font-weight:bold">{e(rs.service_name)}</div>'
        f'<div style="color:#444">{e(intro)}</div></div>']
    for kind in kinds:
        heading = KINDS[kind][0]
        text += [heading.upper(), "=" * len(heading), ""]
        body.append(f'<h1 style="font-size:1.3em;margin:28px 0 8px;color:{e(accent)}">{e(heading)}</h1>')
        for k, sec in parts:
            if k != kind:
                continue
            if sec.title != heading:
                text += [sec.title, "-" * len(sec.title)]
                body.append(f'<h2 style="font-size:1.1em;margin:16px 0 6px">{e(sec.title)}</h2>')
            for w in sec.warnings:
                text.append(f"Note: {w}")
                body.append(f'<p style="background:#fff4e5;border-left:4px solid #b25e00;padding:8px 10px;margin:8px 0">'
                            f"<strong>Note:</strong> {e(w)}</p>")
            text += sec.lines
            body += [f"<p style=\"margin:4px 0\">{e(line)}</p>" for line in sec.lines]
            for t in sec.tables:
                text += [""] + _text_table(t)
                body.append(_html_table(t))
            if sec.link:
                text += ["", f"{sec.link[0]}: {sec.link[1]}"]
                body.append(f'<p><a href="{e(sec.link[1])}">{e(sec.link[0])}</a></p>')
            text.append("")
    settings_url = _url("/settings")
    stops = [(KINDS[k][0], _url(f"/unsubscribe?t={unsubscribe_token(user, k)}")) for k in kinds]
    org = rs.service_name + (f", {rs.organization_name}" if rs.organization_name else "")
    text += ["--", f"You get this {frequency} email from {org}.", f"Choose which reports you get and how often: {settings_url}"]
    text += [f"Stop \"{name}\" emails: {url}" for name, url in stops]
    body.append('<hr><p style="font-size:0.9em;color:#444">'
                f"You get this {e(frequency)} email from {e(org)}. "
                f'<a href="{e(settings_url)}">Choose which reports you get and how often</a>.<br>'
                + "<br>".join(f'<a href="{e(url)}">Stop &ldquo;{e(name)}&rdquo; emails</a>' for name, url in stops)
                + "</p>")
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + e(subject) + "</title></head>"
            '<body style="font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;line-height:1.45;max-width:680px">'
            + "".join(body) + "</body></html>")
    return Mail(to=user.email, subject=subject, text="\n".join(text).rstrip() + "\n", html=page)


# --- sending -----------------------------------------------------------------------------------------

def _due_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.status == "active")))


def send_due(db: Session, now: datetime | None = None) -> int:
    """Worker: queue each person's reports whose period is due and not yet sent. Returns emails queued."""
    rs = settings_store.load(db)
    if not rs.email_reports_enabled:
        return 0
    now = now or _now()
    zone = ZoneInfo(rs.report_time_zone)
    queued = 0
    periods = {f: latest_period(f, now, zone, rs.report_send_hour) for f in ("daily", "weekly", "monthly")}
    for user in _due_users(db):
        by_frequency: dict[str, list[str]] = defaultdict(list)
        for kind, freq in choices(db, user).items():
            if freq != "off":
                by_frequency[freq].append(kind)
        for freq, kinds in by_frequency.items():
            start, end, due = periods[freq]
            if now - due > CATCH_UP[freq] or user.created_at >= end:
                continue
            if db.scalar(select(ReportSend.id).where(ReportSend.user_id == user.id, ReportSend.frequency == freq,
                                                     ReportSend.period_start == start)):
                continue
            try:
                parts = build(db, user, kinds, start, end)
                send = ReportSend(user_id=user.id, email=user.email, frequency=freq, period_start=start,
                                  period_end=end, kinds=kinds, status="queued" if parts else "skipped")
                if parts:
                    mail = compose(db, user, freq, start, end, parts)
                    row = MailOutbox(kind="report", to_addr=mail.to, subject=mail.subject, body=mail.text,
                                     html=mail.html, from_name=rs.mail_from_name, status="queued", next_try_at=_now())
                    db.add(row)
                    db.flush()
                    send.outbox_id = row.id
                    queued += 1
                db.add(send)
                db.commit()
            except IntegrityError:  # another worker sent it first
                db.rollback()
            except Exception:  # noqa: BLE001 - one person's report never stops the others
                db.rollback()
                log.exception("couldn't build the %s report for %s", freq, user.email)
    return queued


def send_preview(db: Session, user: User) -> str:
    """Send someone their reports for the latest monthly period now, to see what they look like. Not recorded
    as the scheduled send. At most one a minute."""
    recent = db.scalar(select(func.count()).select_from(MailOutbox).where(
        MailOutbox.kind == "report_preview", MailOutbox.to_addr == user.email,
        MailOutbox.created_at > _now() - timedelta(minutes=1)))
    if recent:
        raise ApiError(429, "too_soon", "A sample was just sent. Wait a minute before asking for another.")
    kinds = eligible(user)
    if not kinds:
        raise ApiError(409, "no_reports", "No email reports apply to you.")
    rs = settings_store.load(db)
    start, end, _ = latest_period("monthly", _now(), ZoneInfo(rs.report_time_zone), rs.report_send_hour)
    parts = build(db, user, kinds, start, end)
    if not parts:
        parts = [("my_month", Section("Your documents", ["Nothing to report for this period."],
                                      link=("See your documents", _url("/"))))]
    mail = compose(db, user, "monthly", start, end, parts)
    mail.subject = "Sample: " + mail.subject
    outbox.queue(db, [mail], rs.mail_from_name, "report_preview")
    return mail.subject
