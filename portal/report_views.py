"""Scheduled report views: an admin saves a view of a live report (its filters, search, sort, columns and group)
to be emailed monthly, weekly or daily, to themselves and other admins with the same access.

Each period the view is run for the period just ended (its own date range is replaced by the period) and sent to
each recipient who still has the same access *at send time*: super admins for super-admin reports or every group;
for one group, its admins (and super admins). If the owner has lost access, it isn't sent at all. One email per
view per recipient per period, recorded like the built-in reports, with the first rows, the totals, a link to the
live view, and optionally the whole view as a CSV attachment.
"""
from __future__ import annotations

import csv
import html
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import audit, email_reports, live_reports, outbox, permissions, settings_store
from portal.config import get_portal_settings
from portal.email_reports import CATCH_UP, latest_period, period_label
from portal.errors import ApiError, not_found
from portal.mail import Mail, attachment_limit
from portal.models import MailOutbox, ReportSend, RoleAssignment, SavedReport, User

log = logging.getLogger(__name__)

FREQUENCIES = ("monthly", "weekly", "daily")
ROWS_IN_EMAIL = 50
MAX_ATTACHMENT = 5 * 1024 * 1024  # bigger CSVs are left out, with a note to download it from the app
RESERVED = {"report", "range", "since", "until", "page", "page_size", "group_id"}  # set by the schedule, not saved


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- access --------------------------------------------------------------------------------------------

def scope(view: SavedReport) -> list[uuid.UUID] | None | bool:
    """The groups the view covers, as its owner can see them now: None = every group; False = the owner can't
    see this view any more (so it isn't sent)."""
    owner = view.owner
    if owner is None or owner.status != "active":
        return False
    report = live_reports.REPORTS.get(view.report)
    every = permissions.has(owner, permissions.REPORTS)  # super admins and service admins (#564)
    if report is None or (report.super_only and not every):
        return False
    if view.group_id is not None:
        return [view.group_id] if every or view.group_id in owner.admin_group_ids() else False
    if every:
        return None
    ids = sorted(owner.admin_group_ids(), key=str)
    return ids or False


def can_receive(user: User, view: SavedReport, groups: list[uuid.UUID] | None) -> bool:
    """Someone may get a view if they could see the same thing in the app."""
    if user.status != "active":
        return False
    if permissions.has(user, permissions.REPORTS):
        return True
    report = live_reports.REPORTS.get(view.report)
    if groups is None or report is None or report.super_only:
        return False
    return set(groups) <= user.admin_group_ids()


def candidates(db: Session, view: SavedReport) -> list[User]:
    """Who could get this view: everyone active with the same access."""
    groups = scope(view)
    if groups is False:
        return []
    ids = {r.user_id for r in db.scalars(select(RoleAssignment).where(
        RoleAssignment.role.in_(("super_admin", "service_admin", "group_admin"))))}
    users = db.scalars(select(User).where(User.id.in_(ids), User.status == "active").order_by(User.email)).all()
    return [u for u in users if can_receive(u, view, groups)]


# --- saving --------------------------------------------------------------------------------------------

def _clean(report: str, params: dict) -> dict:
    return {str(k)[:40]: str(v)[:500] for k, v in params.items() if k not in RESERVED and v not in (None, "")}


def _check(db: Session, view: SavedReport, recipient_ids: list[str]) -> list[str]:
    if view.frequency not in FREQUENCIES:
        raise ApiError(422, "bad_frequency", "Choose monthly, weekly or daily.")
    if not view.name.strip():
        raise ApiError(422, "name_required", "Give the view a name.")
    groups = scope(view)
    if groups is False:
        raise not_found("Report")
    allowed = {str(u.id) for u in candidates(db, view)}
    wanted = list(dict.fromkeys(str(r) for r in recipient_ids))
    bad = [r for r in wanted if r not in allowed]
    if bad:
        raise ApiError(422, "bad_recipients", "Some recipients can't see this report, so it can't be sent to them.")
    return wanted


def create(db: Session, request: Request | None, owner: User, *, name: str, report: str, params: dict,
           group_id: uuid.UUID | None, frequency: str, recipients: list[str], attach_csv: bool) -> SavedReport:
    if report not in live_reports.REPORTS:
        raise not_found("Report")
    view = SavedReport(id=uuid.uuid4(), owner_id=owner.id, owner=owner, name=name.strip()[:120], report=report,
                       params=_clean(report, params), group_id=group_id, frequency=frequency, attach_csv=attach_csv)
    view.recipients = _check(db, view, recipients or [str(owner.id)])
    db.add(view)
    audit.record(db, request, "report_view.created", actor=owner, target_type="report_view", target_id=view.id,
                 group_id=group_id, details=_details(db, view))
    return view


def update(db: Session, request: Request | None, view: SavedReport, actor: User, changes: dict) -> SavedReport:
    for key in ("name", "frequency", "attach_csv"):
        if key in changes and changes[key] is not None:
            setattr(view, key, changes[key].strip()[:120] if key == "name" else changes[key])
    recipients = changes.get("recipients")
    view.recipients = _check(db, view, recipients if recipients is not None else view.recipients)
    view.updated_at = _now()
    audit.record(db, request, "report_view.changed", actor=actor, target_type="report_view", target_id=view.id,
                 group_id=view.group_id, details=_details(db, view))
    return view


def delete(db: Session, request: Request | None, view: SavedReport, actor: User) -> None:
    audit.record(db, request, "report_view.deleted", actor=actor, target_type="report_view", target_id=view.id,
                 group_id=view.group_id, details={"name": view.name, "report": view.report})
    db.delete(view)


def leave(db: Session, request: Request | None, view: SavedReport, user: User, how: str) -> None:
    """Stop getting a view someone else (or you) set up."""
    if str(user.id) not in view.recipients:
        return
    view.recipients = [r for r in view.recipients if r != str(user.id)]
    audit.record(db, request, "report_view.left", actor=user, target_type="report_view", target_id=view.id,
                 group_id=view.group_id, details={"name": view.name, "how": how})


def _details(db: Session, view: SavedReport) -> dict:
    emails = [u.email for u in db.scalars(select(User).where(User.id.in_([uuid.UUID(r) for r in view.recipients])))]
    return {"name": view.name, "report": view.report, "frequency": view.frequency, "recipients": sorted(emails),
            "attach_csv": view.attach_csv, "params": view.params}


def get_for(db: Session, view_id: uuid.UUID, user: User, owner_only: bool = False) -> SavedReport:
    view = db.get(SavedReport, view_id)
    if view is None or (view.owner_id != user.id and (owner_only or str(user.id) not in view.recipients)):
        raise not_found("Report view")
    return view


# --- the email -----------------------------------------------------------------------------------------

def _query(view: SavedReport, start: datetime, end: datetime, page_size: int) -> live_reports.Query:
    p = dict(view.params)
    cols = p.pop("cols", None)
    return live_reports.Query(since=start, until=end, q=p.pop("q", ""), sort=p.pop("sort", ""), page=1,
                              page_size=page_size, cols=[c for c in cols.split(",") if c] if cols else None, filters=p)


def _fmt(kind: str, v, period: str | None, zone: ZoneInfo | None = None, labels: dict | None = None) -> str:
    if v is None or v == "":
        return "–"
    if labels and str(v) in labels:  # a filter's own words for a value, as the app shows them ("Done", not "completed")
        return labels[str(v)]
    if kind == "date" and isinstance(v, str):
        try:
            d = datetime.fromisoformat(v)
            return (d.astimezone(zone) if zone and d.tzinfo else d).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return v[:16].replace("T", " ")
    if kind == "period" and isinstance(v, str):
        return v[:7] if period == "month" else v[:10]
    if kind == "money":
        return f"${float(v):,.2f}"
    if kind == "pct":
        return f"{v}%"
    if kind == "hours":
        return f"{float(v):.1f} h"
    if kind == "bool":
        return "Yes" if v else "No"
    if kind == "num" and isinstance(v, (int, float)):
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return str(v)


def link(view: SavedReport, start: datetime, end: datetime, zone: ZoneInfo) -> str:
    """The same view in the app, for the period."""
    last_day = (end.astimezone(zone) - timedelta(days=1)).date()
    q = {"report": view.report, "range": "custom", "since": start.astimezone(zone).date().isoformat(),
         "until": last_day.isoformat(), **view.params}
    base = f"/admin/groups/{view.group_id}/reports" if view.group_id else "/admin/reports"
    return get_portal_settings().app_url.rstrip("/") + base + "?" + urlencode(q)


def compose(db: Session, view: SavedReport, recipient: User, frequency: str, start: datetime, end: datetime,
            groups: list[uuid.UUID] | None, sample: bool = False) -> Mail:
    rs = settings_store.load(db)
    zone = ZoneInfo(rs.report_time_zone)
    is_super = permissions.has(view.owner, permissions.REPORTS)
    data = live_reports.run(db, view.report, _query(view, start, end, ROWS_IN_EMAIL), groups, is_super)
    shown = [c for c in data["columns"] if c["shown"]]
    label = period_label(frequency, start, end, zone)
    subject = ("Sample: " if sample else "") + f"{view.name}: {label}"
    url = link(view, start, end, zone)
    e = html.escape
    accent = rs.accent_color or "#1f4e8c"
    lines = [f"{data['label']} for {label}" + (", filtered as saved" if view.params else "") +
             f". Times are {rs.report_time_zone}."]
    lines += [f"{h['label']}: {h['value']}" + (f" ({h['hint']})" if h.get("hint") else "") for h in data["headline"]]
    total_rows = data["total_rows"]
    words = {f["key"]: {o["value"]: o["label"] for o in f["options"]} for f in data["filters"]}
    rows = [[_fmt(c["kind"], r.get(c["key"]), data.get("period"), zone, words.get(c["key"])) for c in shown]
            for r in data["rows"]]
    totals = [_fmt(c["kind"], data["totals"][c["key"]], None) if c["key"] in data["totals"] else "" for c in shown]
    if data["totals"] and shown:
        totals[0] = totals[0] or "Total"
    showing = (f"All {total_rows:,} rows." if total_rows <= ROWS_IN_EMAIL else
               f"The first {ROWS_IN_EMAIL} of {total_rows:,} rows; the rest are in the app" +
               (" and the attached CSV." if view.attach_csv else "."))

    attachments: list[tuple[str, str, bytes]] = []
    csv_note = ""
    if view.attach_csv:
        header, all_rows, _ = live_reports.export(db, view.report, _query(view, start, end, live_reports.MAX_PAGE),
                                                  groups, is_super)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(all_rows)
        data_bytes = buf.getvalue().encode("utf-8-sig")
        name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in view.name.lower()).strip("-") or "report"
        limit = attachment_limit()
        if limit == 0:
            csv_note = "This mail service can't send attachments: download the CSV from the report in the app."
        elif len(data_bytes) > min(MAX_ATTACHMENT, limit or MAX_ATTACHMENT):
            csv_note = "The CSV is too big to attach: download it from the report in the app."
        else:
            attachments.append((f"{name}-{start.astimezone(zone).date().isoformat()}.csv", "text/csv; charset=utf-8",
                                data_bytes))

    # Plain text
    text = [subject, "", *lines, ""]
    if rows:
        widths = [max(len(str(x)) for x in col) for col in zip([c["label"] for c in shown], *rows, totals)]
        fmt = lambda r: "  ".join(str(x).ljust(widths[i]) for i, x in enumerate(r)).rstrip()  # noqa: E731
        text += [fmt([c["label"] for c in shown]), *[fmt(r) for r in rows]]
        if data["totals"]:
            text.append(fmt(totals))
        text += ["", showing]
    else:
        text.append("Nothing in this period.")
    if csv_note:
        text.append(csv_note)
    stop = email_reports.unsubscribe_token(recipient, f"view:{view.id}")
    stop_url = get_portal_settings().app_url.rstrip("/") + f"/unsubscribe?t={stop}"
    text += ["", f"Open it in the app: {url}", "--",
             f"{view.owner.email} set up this {frequency} report{' for you' if view.owner_id != recipient.id else ''}.",
             f"Stop getting it: {stop_url}"]

    # HTML
    cell = "padding:6px 10px;border-bottom:1px solid #d9dde3;"
    num = {"num", "money", "pct", "hours"}

    def td(i: int, c: dict, v: str, head: bool = False, tag: str = "td") -> str:
        align = "right" if c["kind"] in num else "left"
        weight = "font-weight:bold;" if head else "font-weight:normal;"
        bg = "background:#f2f4f7;" if head else ""
        scope_attr = ' scope="col"' if tag == "th" and head else (' scope="row"' if tag == "th" else "")
        return f'<{tag}{scope_attr} style="{cell}text-align:{align};{weight}{bg}">{e(v)}</{tag}>'
    table = ""
    if rows:
        head = "".join(td(i, c, c["label"], True, "th") for i, c in enumerate(shown))
        body = "".join("<tr>" + "".join(td(i, c, r[i], tag="th" if i == 0 else "td") for i, c in enumerate(shown))
                       + "</tr>" for r in rows)
        foot = ("<tfoot><tr>" + "".join(td(i, c, totals[i], True, "th" if i == 0 else "td") for i, c in enumerate(shown))
                + "</tr></tfoot>") if data["totals"] else ""
        table = (f'<table style="border-collapse:collapse;margin:8px 0 12px"><caption style="text-align:left;'
                 f'font-weight:bold;padding:4px 0 6px">{e(data["label"])}</caption><thead><tr>{head}</tr></thead>'
                 f"<tbody>{body}</tbody>{foot}</table><p>{e(showing)}</p>")
    else:
        table = "<p>Nothing in this period.</p>"
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + e(subject) + "</title></head>"
            '<body style="font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;line-height:1.45">'
            f'<div style="border-bottom:3px solid {e(accent)};padding:0 0 10px;margin:0 0 16px">'
            f'<div style="font-size:1.25em;font-weight:bold">{e(view.name)}</div>'
            f'<div style="color:#444">{e(rs.service_name)}, {e(label)}</div></div>'
            + "".join(f'<p style="margin:4px 0">{e(line)}</p>' for line in lines) + table
            + (f"<p>{e(csv_note)}</p>" if csv_note else "")
            + f'<p><a href="{e(url)}">Open it in the app</a></p>'
            + '<hr><p style="font-size:0.9em;color:#444">'
            + f"{e(view.owner.email)} set up this {e(frequency)} report"
            + (" for you" if view.owner_id != recipient.id else "") + f'. <a href="{e(stop_url)}">Stop getting it</a>.</p>'
            + "</body></html>")
    return Mail(to=recipient.email, subject=subject, text="\n".join(text) + "\n", html=page, attachments=attachments)


# --- sending -------------------------------------------------------------------------------------------

def send_due(db: Session, now: datetime | None = None) -> int:
    """Worker (with the built-in reports): queue each view's email for the period just ended, once per recipient."""
    rs = settings_store.load(db)
    if not rs.email_reports_enabled:
        return 0
    now = now or _now()
    zone = ZoneInfo(rs.report_time_zone)
    queued = 0
    for view in db.scalars(select(SavedReport)).all():
        start, end, due = latest_period(view.frequency, now, zone, rs.report_send_hour)
        if now - due > CATCH_UP[view.frequency] or view.created_at >= end:
            continue
        groups = scope(view)
        if groups is False:
            continue
        sent = {str(u) for u in db.scalars(select(ReportSend.user_id).where(
            ReportSend.saved_report_id == view.id, ReportSend.period_start == start))}
        for rid in view.recipients:
            if rid in sent:
                continue
            user = db.get(User, uuid.UUID(rid))
            if user is None or not can_receive(user, view, groups):
                continue
            try:
                mail = compose(db, view, user, view.frequency, start, end, groups)
                row = MailOutbox(kind="report_view", to_addr=mail.to, subject=mail.subject, body=mail.text,
                                 html=mail.html, attachments=outbox.encode_attachments(mail),
                                 from_name=rs.mail_from_name, status="queued", next_try_at=_now())
                db.add(row)
                db.flush()
                db.add(ReportSend(user_id=user.id, email=user.email, frequency=view.frequency, period_start=start,
                                  period_end=end, kinds=[f"view: {view.name}"], status="queued", outbox_id=row.id,
                                  saved_report_id=view.id))
                db.commit()
                queued += 1
            except IntegrityError:
                db.rollback()
            except Exception:  # noqa: BLE001 - one view never stops the others
                db.rollback()
                log.exception("couldn't send report view %s to %s", view.id, rid)
    return queued


def send_sample(db: Session, view: SavedReport, user: User) -> str:
    recent = db.scalar(select(func.count()).select_from(MailOutbox).where(
        MailOutbox.kind == "report_preview", MailOutbox.to_addr == user.email,
        MailOutbox.created_at > _now() - timedelta(minutes=1)))
    if recent:
        raise ApiError(429, "too_soon", "A sample was just sent. Wait a minute before asking for another.")
    groups = scope(view)
    if groups is False or not can_receive(user, view, groups):
        raise not_found("Report view")
    rs = settings_store.load(db)
    start, end, _ = latest_period(view.frequency, _now(), ZoneInfo(rs.report_time_zone), rs.report_send_hour)
    mail = compose(db, view, user, view.frequency, start, end, groups, sample=True)
    outbox.queue(db, [mail], rs.mail_from_name, "report_preview")
    return mail.subject
