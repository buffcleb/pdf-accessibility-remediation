"""Live reports: tables admins can filter, search, sort and page through in the app, and export as they see them.

Each report is one SQL query declared as columns, filters and search fields. The same definition serves the
table (one page at a time, with totals and headline numbers over every matching row) and the CSV export, so
what's exported is what's on screen. Group admins see their groups' rows only; cost columns are for super admins.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import DateTime, Float, Integer, Numeric, Select, String, Text, and_, case, cast, distinct, func, or_, select
from sqlalchemy import column, literal_column
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from portal.errors import ApiError
from portal.models import (
    LEDGER_GROUP, AiKind, AuditEvent, Group, GroupService, HelpRequest, Job, MailOutbox, QuotaLedger, ReportSend, Service, User, UserService,
)

MAX_PAGE = 200
PERIODS = ("month", "week", "day", "hour")
MAX_EXPORT = 100_000


@dataclass(eq=False)
class Col:
    key: str
    label: str
    expr: ColumnElement
    kind: str = "text"  # text | num | money | date | bool | hours
    total: bool = False  # summed over every matching row
    default: bool = True  # shown unless the viewer hides it
    super_only: bool = False  # for those who see every report (reports.all): super admins and service admins (#564)
    link: str | None = None  # job | group | person | help: the app turns the value into a link


@dataclass
class Filter:
    key: str
    label: str
    apply: Callable[[Select, str], Select]
    options: Callable[[Session], list[tuple[str, str]]] | list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Ctx:
    since: datetime | None
    until: datetime | None
    by: str = "month"  # period of grouped reports: month, week or day


@dataclass
class Measure:
    key: str
    label: str
    unit: str = ""  # "%", "$", "pages"…
    num: str = ""  # the column summed
    den: str | None = None  # when set: sum(num) / sum(den) × scale (a rate, never a sum of rates)
    scale: float = 1


@dataclass
class Chart:
    """What the app draws above the table, computed over every matching row (not just the page)."""
    x: str  # column for the x axis (a period, or a category for kind=hbar)
    kind: str  # line | bar | hbar
    measures: list[Measure]  # one shown at a time: never two scales on one chart
    series: str | None = None  # column that splits the data into lines or bars
    ref: str | None = None  # a column drawn as a reference line (a daily limit), when there's one series
    limit: int = 12  # hbar: top N


@dataclass
class Report:
    name: str
    label: str
    description: str
    base: Callable[[Ctx], Select]  # FROM and joins, no columns
    cols: list[Col]
    group_col: ColumnElement
    date_col: ColumnElement | None  # None: the date range applies inside the columns (usage)
    search: list[ColumnElement]
    filters: list[Filter]
    sort: str  # default sort, e.g. "-created_at"
    links: dict[str, ColumnElement] = field(default_factory=dict)  # ids the app needs for links
    search_hint: str = ""  # what the search box looks through, for its placeholder
    headline: Callable[[Session, Select], list[dict]] | None = None
    cols_fn: Callable[[Ctx], list[Col]] | None = None  # columns that depend on the request (period, date range)
    group_by: list[str] | None = None  # grouped reports: the columns (by key) that make one row
    super_only: bool = False  # as Col.super_only
    chart: Chart | None = None


def _yes_no(expr) -> Callable[[Select, str], Select]:
    return lambda stmt, v: stmt.where(expr.is_(True)) if v == "yes" else stmt.where(expr.is_not(True)) if v == "no" else stmt


def _eq(expr) -> Callable[[Select, str], Select]:
    return lambda stmt, v: stmt.where(expr == v)


def _services(db: Session) -> list[tuple[str, str]]:
    return [(s.key, s.label) for s in db.scalars(select(Service).order_by(Service.sort, Service.label))]


def _distinct(expr) -> Callable[[Session], list[tuple[str, str]]]:
    def opts(db: Session):
        return [(v, v) for v in db.scalars(select(expr).where(expr.is_not(None)).distinct().order_by(expr).limit(200))]
    return opts


YES_NO = [("yes", "Yes"), ("no", "No")]


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


# --- documents ---------------------------------------------------------------------------------------

def _kind_expr() -> ColumnElement:
    """The kind of document within the AI level, by name ("General" when none was chosen); no AI: empty."""
    label = select(AiKind.label).where(AiKind.service_key == Job.ai_tier, AiKind.key == Job.ai_kind).scalar_subquery()
    return case((Job.ai_tier.is_(None), None), (Job.ai_kind.is_(None), "General"), else_=func.coalesce(label, Job.ai_kind))



_summary = Job.result_summary
# The limits a document reached (#312): the engine's and the portal's (result_summary.metrics.limits_hit), and a
# failure for taking too long or running the worker out of memory
_limits_raw = _summary["metrics"]["limits_hit"]
# (a list, or an empty one: older summaries have none, or a JSON null)
_limits_json = case((func.jsonb_typeof(_limits_raw) == "array", _limits_raw), else_=func.jsonb_build_array())
_FAILED_LIMITS = {"time_limit_exceeded": "TASK_TIME_LIMIT_S", "worker_lost": "worker memory (lost worker)"}
_hit_any = or_(func.jsonb_array_length(_limits_json) > 0, Job.error_code.in_(list(_FAILED_LIMITS)))


def _limits_named() -> ColumnElement:
    """The settings a document reached, by name: "AI_NOTATION_MAX, VERIFY_AI_MAX_ITEMS"."""
    each = func.jsonb_array_elements(_limits_json).table_valued(column("value", JSONB)).alias("hit")
    names = select(func.string_agg(each.c.value["setting"].astext, ", ")).select_from(each).scalar_subquery()
    failed = case(*[(Job.error_code == code, name) for code, name in _FAILED_LIMITS.items()], else_=None)
    return func.nullif(func.concat_ws(", ", names, failed), "")


_doc_compliant = _summary["compliant_after"].as_boolean()
_doc_findings = cast(_summary["review"]["findings_total"].as_string(), Integer)


def _doc_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(
        func.count(), func.coalesce(func.sum(sub.c.pages_charged), 0),
        func.count().filter(and_(sub.c.status == "completed", sub.c.report_only.is_not(True))),
        func.count().filter(and_(sub.c.status == "completed", sub.c.report_only.is_not(True), sub.c.compliant.is_(True))),
        func.count().filter(sub.c.findings > 0),
    )).one()
    rate = round(100 * row[3] / row[2]) if row[2] else None
    return [{"label": "Documents", "value": row[0]}, {"label": "Pages charged", "value": int(row[1])},
            {"label": "Pass PDF/UA", "value": f"{rate}%" if rate is not None else "–",
             "hint": "of finished, remediated documents (PDF/UA-1; PDF/UA-2 for PDF 2.0 documents)"},
            {"label": "With review findings", "value": row[4]}]


DOCUMENTS = Report(
    name="documents", label="Documents", description="Every document: service, pages, result and review findings.",
    base=lambda ctx: select().select_from(Job).join(User, User.id == Job.user_id).outerjoin(Group, Group.id == Job.group_id),
    cols=[
        Col("created_at", "Submitted", Job.created_at, "date"),
        Col("filename", "Document", Job.filename, link="job"),
        Col("person", "Person", User.email, link="person"),
        Col("group", "Group", Group.name, link="group"),
        Col("processing", "Service", Job.processing, default=False),
        Col("ai_tier", "AI level", Job.ai_tier, default=False),
        Col("ai_kind", "Kind of document", _kind_expr(), default=False),
        Col("report_only", "Audit only", Job.report_only, "bool", default=False),
        Col("status", "Status", Job.status),
        Col("pages", "Pages", Job.pages, "num", total=True, default=False),
        Col("pages_charged", "Pages charged", Job.pages_charged, "num", total=True),
        Col("compliant", "Passes PDF/UA", _doc_compliant, "bool"),
        Col("failed_checks", "Failed checks after", cast(_summary["failed_checks_after"].as_string(), Integer), "num", default=False),
        Col("findings", "Review findings", _doc_findings, "num", total=True),
        Col("seconds", "Time to process (s)", func.round(cast(func.extract("epoch", Job.finished_at - Job.submitted_at), Float)),
            "num", default=False),
        Col("external", "Sent elsewhere", func.jsonb_array_length(Job.external_services) > 0, "bool", default=False),
        Col("ai_usd", "AI cost ($)", cast(Job.cost["ai_usd"].as_string(), Float), "money", total=True, super_only=True,
            default=False),
        Col("limits", "Limits hit", _limits_named(), super_only=True, default=False),
    ],
    group_col=Job.group_id, date_col=Job.created_at,
    search=[Job.filename, User.email, User.name],
    filters=[
        Filter("status", "Status", _eq(Job.status), [
            ("completed", "Done"), ("failed", "Failed"), ("processing", "Processing"), ("submitted", "Sent"),
            ("queued", "Waiting"), ("held", "Waiting for confirmation"), ("canceled", "Canceled")]),
        Filter("service", "Service or AI level", lambda st, v: st.where(or_(Job.processing == v, Job.ai_tier == v)), _services),
        Filter("outcome", "Result", lambda st, v: st.where(
            _doc_compliant.is_(True) if v == "pass" else _doc_compliant.is_(False) if v == "fail" else _doc_findings > 0),
            [("pass", "Passes PDF/UA"), ("fail", "Doesn't pass"), ("flagged", "Has review findings")]),
        Filter("report_only", "Audit only", _yes_no(Job.report_only), YES_NO),
        Filter("external", "Sent elsewhere", lambda st, v: st.where(
            (func.jsonb_array_length(Job.external_services) > 0) if v == "yes" else (func.jsonb_array_length(Job.external_services) == 0)), YES_NO),
        Filter("limits", "Hit a limit", lambda st, v: st.where(_hit_any if v == "yes" else ~_hit_any), YES_NO),
    ],
    sort="-created_at", links={"job_id": Job.id, "group_id": Job.group_id, "user_id": Job.user_id},
    search_hint="document or person",
    headline=_doc_headline,
)


# --- usage -------------------------------------------------------------------------------------------

def _usage_base(ctx: Ctx) -> Select:
    return (select().select_from(UserService).join(User, User.id == UserService.user_id)
            .outerjoin(Group, Group.id == User.group_id).join(Service, Service.key == UserService.service_key)
            .outerjoin(GroupService, and_(GroupService.group_id == User.group_id,
                                          GroupService.service_key == UserService.service_key)))


def _usage_cols(ctx: Ctx) -> list[Col]:
    """Completed documents (and their pages) in the period charged to the row's service: the period comes from the
    request, so these columns are built per query."""
    cond = [Job.user_id == UserService.user_id, Job.status == "completed",
            Job.charged_services.op("@>")(func.jsonb_build_array(UserService.service_key))]
    if ctx.since:
        cond.append(Job.finished_at >= ctx.since)
    if ctx.until:
        cond.append(Job.finished_at < ctx.until)
    docs = select(func.count()).select_from(Job).where(*cond).correlate(UserService).scalar_subquery()
    pages = select(func.coalesce(func.sum(Job.pages_charged), 0)).select_from(Job).where(*cond).correlate(UserService).scalar_subquery()
    return [
        Col("group", "Group", Group.name, link="group"),
        Col("person", "Person", User.email, link="person"),
        Col("name", "Name", User.name, default=False),
        Col("account", "Account", User.status, default=False),
        Col("approval", "Approval", User.approval, default=False),
        Col("service", "Service", Service.label),
        Col("limit", "Limit", func.coalesce(UserService.cap_pages, GroupService.default_user_cap), "num"),
        Col("own_limit", "Own limit", UserService.cap_pages.is_not(None), "bool", default=False),
        Col("used", "Pages used (all time)", UserService.used_pages, "num", total=True),
        Col("reserved", "In progress", UserService.reserved_pages, "num", total=True, default=False),
        Col("docs_period", "Documents in period", docs, "num", total=True),
        Col("pages_period", "Pages in period", pages, "num", total=True),
    ]


def _usage_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count(distinct(sub.c.person)), func.coalesce(func.sum(sub.c.used), 0),
                            func.coalesce(func.sum(sub.c.pages_period), 0))).one()
    return [{"label": "People", "value": row[0]}, {"label": "Pages used (all time)", "value": int(row[1])},
            {"label": "Pages in period", "value": int(row[2])}]


USAGE = Report(
    name="usage", label="Usage", description="Each person's limit and use, per service; documents and pages in the period.",
    base=_usage_base, cols=[], cols_fn=lambda ctx: _usage_cols(ctx), group_col=User.group_id, date_col=None, search=[User.email, User.name],
    filters=[
        Filter("service", "Service", _eq(UserService.service_key), _services),
        Filter("account", "Account", _eq(User.status), [("active", "Active"), ("disabled", "Disabled")]),
        Filter("approval", "Approval", _eq(User.approval), [("approved", "Approved"), ("pending", "Waiting"), ("declined", "Declined")]),
        Filter("own_limit", "Own limit", _yes_no(UserService.cap_pages.is_not(None)), YES_NO),
    ],
    sort="person", links={"group_id": User.group_id, "user_id": User.id}, headline=_usage_headline,
    search_hint="person",
)


# --- page ledger -------------------------------------------------------------------------------------

_Person = User.__table__.alias("ledger_person")


def _ledger_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(
        func.coalesce(func.sum(sub.c.pages).filter(sub.c.kind == "topup"), 0),
        func.coalesce(func.sum(sub.c.pages).filter(sub.c.kind == "commit"), 0),
        func.coalesce(-func.sum(sub.c.pages).filter(and_(sub.c.kind == "correct", sub.c.pages < 0)), 0),
        func.count())).one()
    return [{"label": "Pages added", "value": int(row[0])}, {"label": "Pages used", "value": int(row[1])},
            {"label": "Pages given back", "value": int(row[2]), "hint": "usage corrections"},
            {"label": "Entries", "value": row[3]}]


LEDGER = Report(
    name="ledger", label="Page ledger", description="Every top-up, use, refund and correction, with tickets and reasons.",
    base=lambda ctx: (select().select_from(QuotaLedger).outerjoin(Group, Group.id == LEDGER_GROUP)
                      .outerjoin(_Person, _Person.c.id == QuotaLedger.user_id)),
    cols=[
        Col("created_at", "When", QuotaLedger.created_at, "date"),
        Col("kind", "Entry", QuotaLedger.kind),
        Col("service", "Service", QuotaLedger.service_key, default=False),
        Col("scope", "Scope", QuotaLedger.scope, default=False),
        Col("group", "Group", Group.name, link="group"),
        Col("person", "Person", _Person.c.email, link="person"),
        Col("pages", "Pages", QuotaLedger.pages, "num", total=True),
        Col("ticket", "Ticket", QuotaLedger.ticket_ref),
        Col("override", "Override", QuotaLedger.override, "bool", default=False),
        Col("comment", "Reason", QuotaLedger.comment),
        Col("by", "By", QuotaLedger.actor_email),
        Col("pool_after", "Pool after", QuotaLedger.pool_after, "num", default=False),
        Col("used_after", "Used after", QuotaLedger.used_after, "num", default=False),
    ],
    group_col=LEDGER_GROUP, date_col=QuotaLedger.created_at,
    search=[QuotaLedger.ticket_ref, QuotaLedger.comment, _Person.c.email, QuotaLedger.actor_email],
    filters=[
        Filter("kind", "Entry", _eq(QuotaLedger.kind), [("topup", "Top-up"), ("adjust", "Pool correction"),
                                                         ("reserve", "Held for a document"), ("commit", "Used"),
                                                         ("release", "Released"), ("correct", "Usage correction"),
                                                         ("cap", "Subgroup limit")]),
        Filter("service", "Service", _eq(QuotaLedger.service_key), _services),
        Filter("override", "Override", _yes_no(QuotaLedger.override), YES_NO),
    ],
    sort="-created_at", links={"group_id": LEDGER_GROUP, "job_id": QuotaLedger.job_id, "user_id": QuotaLedger.user_id},
    search_hint="ticket, reason or person",
    headline=_ledger_headline,
)


# --- sign-ins ----------------------------------------------------------------------------------------

SIGN_IN_ACTIONS = ("login", "logout", "logout_all", "login.code_requested", "session.revoked")


def _audit_base(actions: tuple[str, ...] | None) -> Callable[[Ctx], Select]:
    def base(ctx: Ctx) -> Select:
        stmt = select().select_from(AuditEvent).outerjoin(Group, Group.id == AuditEvent.group_id)
        return stmt.where(AuditEvent.action.in_(actions)) if actions else stmt
    return base


def _signin_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count().filter(and_(sub.c.event == "login", sub.c.outcome == "success")),
                            func.count().filter(sub.c.outcome != "success"),
                            func.count(distinct(sub.c.person)))).one()
    return [{"label": "Sign-ins", "value": row[0]}, {"label": "Failed or refused", "value": row[1]},
            {"label": "People", "value": row[2]}]


SIGN_INS = Report(
    name="sign_ins", label="Sign-ins", description="Sign-ins, failed attempts, code requests and sign-outs.",
    base=_audit_base(SIGN_IN_ACTIONS),
    cols=[
        Col("created_at", "When", AuditEvent.created_at, "date"),
        Col("person", "Person", AuditEvent.actor_email, link="person"),
        Col("group", "Group", Group.name, link="group"),
        Col("event", "Event", AuditEvent.action),
        Col("outcome", "Outcome", AuditEvent.outcome),
        Col("ip", "IP address", cast(AuditEvent.ip, String)),
        Col("user_agent", "Browser", AuditEvent.user_agent, default=False),
    ],
    group_col=AuditEvent.group_id, date_col=AuditEvent.created_at, search=[AuditEvent.actor_email, cast(AuditEvent.ip, String)],
    filters=[
        Filter("event", "Event", _eq(AuditEvent.action), [(a, a) for a in SIGN_IN_ACTIONS]),
        Filter("outcome", "Outcome", _eq(AuditEvent.outcome), _distinct(AuditEvent.outcome)),
    ],
    sort="-created_at", links={"group_id": AuditEvent.group_id}, headline=_signin_headline,
    search_hint="person or IP address",
)


# --- email: every email sent, and how it went --------------------------------------------------------

MAIL_KINDS = {"sign_in": "Sign-in code", "job": "Document", "help": "Help request", "digest": "Daily summary",
              "approval": "Approval request", "account": "Account decision", "report": "Email report",
              "report_view": "Scheduled report view", "report_preview": "Report preview", "test": "Test email",
              "api_token": "API token", "alert": "Alert", "staff_alert": "Alert (to a service admin)"}
MAIL_STATUSES = {"sent": "Sent", "failed": "Failed", "queued": "Waiting or retrying", "held": "Held (email paused)",
                 "seen": "Not sent: already seen in the portal", "combined": "Combined into a daily summary",
                 "canceled": "Canceled"}


def _labels(expr, labels: dict[str, str]) -> ColumnElement:
    return case(*[(expr == k, v) for k, v in labels.items()], else_=expr)


def _mail_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count().filter(sub.c.status == "Sent"), func.count().filter(sub.c.status == "Failed"),
                            func.count().filter(sub.c.status == "Waiting or retrying"))).one()
    return [{"label": "Sent", "value": row[0]}, {"label": "Failed", "value": row[1]},
            {"label": "Waiting or retrying", "value": row[2]}]


EMAIL = Report(
    name="email", label="Email",
    description="Every email the service sent or tried to: sign-in codes, document and help notifications, summaries "
                "and reports. The message ID matches the email in your mail provider's and recipients' logs.",
    base=lambda ctx: (select().select_from(MailOutbox).outerjoin(User, User.email == MailOutbox.to_addr)
                      .outerjoin(Group, Group.id == User.group_id)),
    cols=[
        Col("created_at", "When", MailOutbox.created_at, "date"),
        Col("to", "To", MailOutbox.to_addr, link="person"),
        Col("group", "Group", Group.name, link="group"),
        Col("kind", "Kind", _labels(MailOutbox.kind, MAIL_KINDS)),
        Col("subject", "Subject", MailOutbox.subject),
        Col("status", "Status", _labels(MailOutbox.status, MAIL_STATUSES)),
        Col("finished_at", "Sent or given up", MailOutbox.finished_at, "date", default=False),
        Col("attempts", "Tries", MailOutbox.attempts, "num", default=False),
        Col("error", "Last error", MailOutbox.last_error),
        Col("backend", "Sent with", MailOutbox.backend, default=False),
        Col("message_id", "Message ID", MailOutbox.message_id, default=False),
    ],
    group_col=User.group_id, date_col=MailOutbox.created_at,
    search=[MailOutbox.to_addr, MailOutbox.subject, MailOutbox.message_id],
    filters=[
        Filter("kind", "Kind", _eq(MailOutbox.kind), list(MAIL_KINDS.items())),
        Filter("status", "Status", _eq(MailOutbox.status), list(MAIL_STATUSES.items())),
    ],
    sort="-created_at", links={"group_id": User.group_id}, headline=_mail_headline,
    search_hint="address, subject or message ID",
)


# --- help requests -----------------------------------------------------------------------------------

_help_hours = cast(func.extract("epoch", HelpRequest.finished_at - HelpRequest.created_at), Float) / 3600


def _help_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count(), func.count().filter(sub.c.status.in_(("open", "in_progress", "waiting"))),
                            func.count().filter(sub.c.status == "resolved"),
                            func.percentile_cont(0.5).within_group(sub.c.hours))).one()
    median = f"{row[3]:.1f} h" if row[3] is not None else "–"
    return [{"label": "Requests", "value": row[0]}, {"label": "Not finished", "value": row[1]},
            {"label": "Resolved", "value": row[2]}, {"label": "Median time to finish", "value": median}]


HELP = Report(
    name="help", label="Help requests", description="Requests for help: kinds, status and how long they took.",
    base=lambda ctx: (select().select_from(HelpRequest).join(User, User.id == HelpRequest.user_id)
                      .outerjoin(Group, Group.id == HelpRequest.group_id)),
    cols=[
        Col("created_at", "Asked", HelpRequest.created_at, "date"),
        Col("filename", "Document", HelpRequest.filename, link="help"),
        Col("person", "Person", User.email, link="person"),
        Col("group", "Group", Group.name, link="group"),
        Col("kind", "Kind", HelpRequest.kind),
        Col("status", "Status", HelpRequest.status),
        Col("assignee", "Handled by", HelpRequest.assignee_email),
        Col("hours", "Hours to finish", func.round(cast(_help_hours, Numeric), 1), "hours"),
        Col("pages_refunded", "Pages given back", HelpRequest.pages_refunded, "num", total=True),
        Col("saved", "Kept past retention", HelpRequest.saved, "bool", default=False),
    ],
    group_col=HelpRequest.group_id, date_col=HelpRequest.created_at, search=[HelpRequest.filename, User.email],
    filters=[
        Filter("status", "Status", _eq(HelpRequest.status), [("open", "Open"), ("in_progress", "In progress"),
                                                              ("waiting", "Waiting on the person"), ("resolved", "Resolved"),
                                                              ("closed", "Closed")]),
        Filter("kind", "Kind", _eq(HelpRequest.kind), [("alt_text", "Alt text"), ("reading_order", "Reading order"),
                                                        ("tables", "Tables"), ("headings", "Headings"), ("math", "Math"),
                                                        ("visual", "Looks different"), ("other", "Something else")]),
    ],
    sort="-created_at", links={"help_id": HelpRequest.id, "group_id": HelpRequest.group_id, "user_id": HelpRequest.user_id},
    search_hint="document or person",
    headline=_help_headline,
)


# --- audit log ---------------------------------------------------------------------------------------

def _audit_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count(), func.count().filter(sub.c.outcome != "success"),
                            func.count(distinct(sub.c.person)))).one()
    return [{"label": "Events", "value": row[0]}, {"label": "Not successful", "value": row[1]},
            {"label": "People", "value": row[2]}]


AUDIT = Report(
    name="audit", label="Audit log", description="Everything recorded: changes to pages, limits, access, services and documents.",
    base=_audit_base(None),
    cols=[
        Col("created_at", "When", AuditEvent.created_at, "date"),
        Col("person", "Who", AuditEvent.actor_email, link="person"),
        Col("event", "Event", AuditEvent.action),
        Col("outcome", "Outcome", AuditEvent.outcome),
        Col("group", "Group", Group.name, link="group"),
        Col("details", "Details", cast(AuditEvent.details, Text)),
        Col("ip", "IP address", cast(AuditEvent.ip, String), default=False),
    ],
    group_col=AuditEvent.group_id, date_col=AuditEvent.created_at,
    search=[AuditEvent.actor_email, AuditEvent.action, cast(AuditEvent.details, Text), func.host(AuditEvent.ip)],
    filters=[
        Filter("event", "Event", lambda st, v: st.where(AuditEvent.action.startswith(v, autoescape=True)),
               lambda db: [(p, p) for p in sorted({a.split(".")[0] for a in db.scalars(
                   select(AuditEvent.action).distinct().limit(500))})]),
        Filter("outcome", "Outcome", _eq(AuditEvent.outcome), _distinct(AuditEvent.outcome)),
    ],
    sort="-created_at", links={"group_id": AuditEvent.group_id}, headline=_audit_headline,
    search_hint="person, event or details",
)

# --- quality: pass rate over time ------------------------------------------------------------------

def _period(ctx: Ctx, col) -> ColumnElement:
    return func.date_trunc(ctx.by, col)


_ProcService = Service.__table__.alias("proc_service")
_TierService = Service.__table__.alias("tier_service")


def _option_expr() -> ColumnElement:
    """The service, plus the AI level (and kind of document, past General) when there was one, by name:
    "Standard + Premium AI (Math and science)". Never model names."""
    label = select(AiKind.label).where(AiKind.service_key == Job.ai_tier, AiKind.key == Job.ai_kind).scalar_subquery()
    kind = func.coalesce(" (" + func.coalesce(label, Job.ai_kind) + ")", "")
    return func.coalesce(_ProcService.c.label, Job.processing) + func.coalesce(
        " + " + func.coalesce(_TierService.c.label, Job.ai_tier) + kind, "")


def _with_options(stmt: Select) -> Select:
    return (stmt.outerjoin(_ProcService, _ProcService.c.key == Job.processing)
            .outerjoin(_TierService, _TierService.c.key == Job.ai_tier))


_remediated = and_(Job.status == "completed", Job.report_only.is_(False))
_doc_failed = cast(_summary["failed_checks_after"].as_string(), Integer)
PERIOD_FILTER = Filter("by", "By", lambda st, v: st, [("hour", "Hour"), ("day", "Day"), ("week", "Week"), ("month", "Month")])


def _quality_cols(ctx: Ctx) -> list[Col]:
    docs = func.count()
    passed = func.count().filter(_doc_compliant.is_(True))
    return [
        Col("period", "Period", _period(ctx, Job.finished_at), "period"),
        Col("option", "Service and AI level", _option_expr()),
        Col("documents", "Documents", docs, "num", total=True),
        Col("passed", "Pass PDF/UA", passed, "num", total=True),
        Col("pass_rate", "Pass rate", func.round(cast(100.0 * passed / func.nullif(docs, 0), Numeric), 1), "pct"),
        Col("flagged", "With review findings", func.count().filter(_doc_findings > 0), "num", total=True),
        Col("avg_failed", "Failed checks left (average, failing documents)",
            func.round(cast(func.avg(_doc_failed).filter(_doc_compliant.is_(False)), Numeric), 1), "num", default=False),
        Col("pages", "Pages", func.sum(Job.pages_charged), "num", total=True, default=False),
    ]


def _quality_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.coalesce(func.sum(sub.c.documents), 0), func.coalesce(func.sum(sub.c.passed), 0),
                            func.coalesce(func.sum(sub.c.flagged), 0))).one()
    rate = f"{100 * row[1] / row[0]:.0f}%" if row[0] else "–"
    return [{"label": "Pass PDF/UA", "value": rate}, {"label": "Remediated documents", "value": int(row[0])},
            {"label": "With review findings", "value": int(row[2])}]


QUALITY = Report(
    name="quality", label="Quality",
    description="How often remediated documents pass PDF/UA (PDF/UA-1; PDF/UA-2 for PDF 2.0 documents), by service "
                "and AI level, over time.",
    base=lambda ctx: _with_options(select().select_from(Job)).where(_remediated, Job.finished_at.is_not(None)),
    cols=[], cols_fn=_quality_cols, group_by=["period", "option"],
    group_col=Job.group_id, date_col=Job.finished_at, search=[],
    filters=[PERIOD_FILTER, Filter("service", "Service or AI level",
                                   lambda st, v: st.where(or_(Job.processing == v, Job.ai_tier == v)), _services)],
    sort="-period", headline=_quality_headline,
    chart=Chart(x="period", kind="line", series="option", measures=[
        Measure("pass_rate", "Pass rate", "%", num="passed", den="documents", scale=100),
        Measure("documents", "Documents", "", num="documents")]),
)


# --- remaining failures: what's left after remediation ---------------------------------------------

_failure = func.jsonb_array_elements(func.coalesce(Job.result_summary["remaining_failures"], func.jsonb_build_array())) \
    .table_valued(column("value", JSONB)).lateral("failure")


def _failure_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.checks), 0))).one()
    top = db.execute(select(sub.c.clause, sub.c.documents).order_by(sub.c.documents.desc()).limit(1)).first()
    return [{"label": "Rules still failing", "value": row[0]}, {"label": "Failed checks", "value": int(row[1])},
            {"label": "Most common", "value": top[0] if top else "–", "hint": f"{top[1]} documents" if top else None}]


FAILURES = Report(
    name="failures", label="Remaining failures",
    description="The PDF/UA rules remediated documents still fail, most common first: where the engine falls short.",
    base=lambda ctx: select().select_from(Job).join(_failure, sa_true()).where(_remediated),
    cols=[
        Col("clause", "Rule (veraPDF clause)", _failure.c.value["clause"].astext),
        Col("description", "What it checks", func.max(_failure.c.value["description"].astext)),
        Col("documents", "Documents", func.count(distinct(Job.id)), "num"),
        Col("checks", "Failed checks", func.sum(cast(_failure.c.value["failed_checks"].astext, Integer)), "num", total=True),
    ],
    group_by=["clause"],
    group_col=Job.group_id, date_col=Job.finished_at,
    search=[_failure.c.value["clause"].astext, _failure.c.value["description"].astext],
    filters=[Filter("service", "Service or AI level",
                    lambda st, v: st.where(or_(Job.processing == v, Job.ai_tier == v)), _services)],
    sort="-documents", headline=_failure_headline, search_hint="rule or description",
    chart=Chart(x="clause", kind="hbar", measures=[Measure("documents", "Documents", "", num="documents")]),
)


# --- cost (super admins) ---------------------------------------------------------------------------

_ai_usd = cast(Job.cost["ai_usd"].as_string(), Float)


def _cost_cols(ctx: Ctx) -> list[Col]:
    return [
        Col("period", "Period", _period(ctx, Job.finished_at), "period"),
        Col("group", "Group", Group.name),
        Col("option", "Service and AI level", _option_expr()),
        Col("documents", "Documents", func.count(), "num", total=True),
        Col("pages", "Pages", func.sum(Job.pages_charged), "num", total=True),
        Col("ai_usd", "AI cost ($)", func.coalesce(func.sum(_ai_usd), 0), "money", total=True),
        Col("adobe_pages", "Adobe pages", func.coalesce(func.sum(Job.pages_charged).filter(_ProcService.c.engine_route == "adobe"), 0),
            "num", total=True),
        # Describe with AI's cost, by the documents' period (the Describe with AI report counts each use when it happens)
        Col("describe_usd", "Describe with AI ($)", func.coalesce(func.sum(cast(Job.cost["describe_usd"].as_string(), Float)), 0),
            "money", total=True, default=False),
    ]


def _cost_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.coalesce(func.sum(sub.c.ai_usd), 0), func.coalesce(func.sum(sub.c.adobe_pages), 0),
                            func.coalesce(func.sum(sub.c.pages), 0))).one()
    return [{"label": "AI cost", "value": f"${float(row[0]):,.2f}"}, {"label": "Adobe pages", "value": int(row[1])},
            {"label": "Pages processed", "value": int(row[2])}]


COST = Report(
    name="cost", label="Cost", description="AI spend and Adobe pages, by group, service and AI level, over time.",
    base=lambda ctx: (_with_options(select().select_from(Job).outerjoin(Group, Group.id == Job.group_id))
                      .where(Job.status == "completed", Job.finished_at.is_not(None))),
    cols=[], cols_fn=_cost_cols, group_by=["period", "group", "option"],
    group_col=Job.group_id, date_col=Job.finished_at, search=[],
    filters=[PERIOD_FILTER, Filter("service", "Service or AI level",
                                   lambda st, v: st.where(or_(Job.processing == v, Job.ai_tier == v)), _services)],
    sort="-period", headline=_cost_headline, super_only=True,
    chart=Chart(x="period", kind="bar", measures=[
        Measure("ai_usd", "AI cost", "$", num="ai_usd"), Measure("adobe_pages", "Adobe pages", "", num="adobe_pages"),
        Measure("pages", "Pages processed", "", num="pages")]),
)


# --- AI usage: what each AI level does and costs ----------------------------------------------------

def _cost_num(key: str, kind=Float) -> ColumnElement:
    return cast(Job.cost[key].as_string(), kind)


DEFAULT_AI = "Engine default AI (no AI level)"


def _level_expr() -> ColumnElement:
    """The AI level, and the kind of document past General, by name: "Premium AI (Math and science)". Documents sent
    without an AI level use the engine's own default AI (its AI_PROVIDER), counted under DEFAULT_AI."""
    label = select(AiKind.label).where(AiKind.service_key == Job.ai_tier, AiKind.key == Job.ai_kind).scalar_subquery()
    kind = func.coalesce(" (" + func.coalesce(label, Job.ai_kind) + ")", "")
    return case((Job.ai_tier.is_(None), DEFAULT_AI), else_=func.coalesce(_TierService.c.label, Job.ai_tier) + kind)


# AI was used: on an AI level, or the engine's default AI (it made calls, or tried to)
_used_ai = or_(Job.ai_tier.is_not(None),
               func.coalesce(cast(Job.cost["ai_calls"].as_string(), Integer),
                             cast(_summary["metrics"]["ai_calls"].as_string(), Integer), 0) > 0,
               cast(Job.cost["ai_failures"].as_string(), Integer) > 0)


def _ai_usage_cols(ctx: Ctx) -> list[Col]:
    docs = func.count()
    pages = func.sum(Job.pages_charged)
    usd = func.coalesce(func.sum(_ai_usd), 0)
    tokens = (func.coalesce(_cost_num("prompt_tokens", Integer), 0) + func.coalesce(_cost_num("completion_tokens", Integer), 0)
              + func.coalesce(_cost_num("review_prompt_tokens", Integer), 0)
              + func.coalesce(_cost_num("review_completion_tokens", Integer), 0))
    # Jobs from before tokens were recorded have none: sums leave them out, and "jobs measured" says how many count
    measured = func.count().filter(Job.cost.has_key("prompt_tokens"))
    seconds = func.coalesce(_cost_num("seconds"), cast(_summary["metrics"]["processing_time_seconds"].as_string(), Float))
    return [
        Col("period", "Period", _period(ctx, Job.finished_at), "period"),
        Col("level", "AI level", _level_expr()),
        Col("writer_model", "Model", func.coalesce(Job.cost["writer_model"].astext, _summary["metrics"]["ai_model"].astext,
                                                   Job.ai_model), super_only=True),
        Col("review_model", "Review model", Job.cost["review_model"].astext, super_only=True, default=False),
        Col("documents", "Documents", docs, "num", total=True),
        Col("pages", "Pages", pages, "num", total=True),
        Col("calls", "AI calls", func.coalesce(func.sum(_cost_num("ai_calls", Integer)), 0), "num", total=True),
        Col("failed", "Failed calls", func.coalesce(func.sum(_cost_num("ai_failures", Integer)), 0), "num", total=True),
        Col("tokens", "Tokens", func.sum(tokens).filter(Job.cost.has_key("prompt_tokens")), "num", total=True),
        Col("prompt_tokens", "Tokens in (writing)", func.sum(_cost_num("prompt_tokens", Integer)), "num", total=True,
            default=False),
        Col("completion_tokens", "Tokens out (writing)", func.sum(_cost_num("completion_tokens", Integer)), "num",
            total=True, default=False),
        Col("review_tokens", "Tokens (review)", func.sum(func.coalesce(_cost_num("review_prompt_tokens", Integer), 0)
                                                         + func.coalesce(_cost_num("review_completion_tokens", Integer), 0))
            .filter(Job.cost.has_key("prompt_tokens")), "num", total=True, default=False),
        Col("measured", "Documents with token counts", measured, "num", total=True, default=False),
        Col("ai_usd", "AI cost ($)", usd, "money", total=True, super_only=True),
        Col("usd_per_doc", "Cost per document ($)", func.round(cast(usd / func.nullif(docs, 0), Numeric), 4), "money",
            super_only=True),
        Col("usd_per_page", "Cost per page ($)", func.round(cast(usd / func.nullif(pages, 0), Numeric), 5), "money",
            super_only=True, default=False),
        Col("review_usd", "Review cost ($)", func.coalesce(func.sum(_cost_num("review_usd")), 0), "money", total=True,
            super_only=True, default=False),
        Col("classifier_usd", "\"Let AI decide\" cost ($)", func.coalesce(func.sum(_cost_num("classifier_usd")), 0),
            "money", total=True, super_only=True, default=False),
        Col("refunded", "AI not charged", func.count().filter(Job.cost["ai_refunded"].as_boolean().is_(True)), "num",
            total=True),
        Col("fallbacks", "Used General instead of the kind", func.count().filter(Job.ai_kind_fallback.is_not(None)), "num",
            total=True, default=False),
        Col("avg_seconds", "Average time (s)", func.round(cast(func.avg(seconds), Numeric), 0), "num"),
    ]


def _ai_usage_headline(db: Session, sub) -> list[dict]:
    calls, failed, tokens, docs = db.execute(select(
        func.coalesce(func.sum(sub.c.calls), 0), func.coalesce(func.sum(sub.c.failed), 0),
        func.coalesce(func.sum(sub.c.tokens), 0), func.coalesce(func.sum(sub.c.documents), 0))).one()
    out = [{"label": "Documents with AI", "value": int(docs)},
           {"label": "AI calls", "value": int(calls), "hint": f"{int(failed)} failed" if failed else None},
           {"label": "Tokens", "value": int(tokens)}]
    if "ai_usd" in sub.c:
        usd = db.scalar(select(func.coalesce(func.sum(sub.c.ai_usd), 0))) or 0
        out.append({"label": "AI cost", "value": f"${float(usd):,.2f}"})
    return out


def _ai_levels(db: Session) -> list[tuple[str, str]]:
    return [(s.key, s.label) for s in db.scalars(select(Service).where(Service.kind == "ai_tier")
                                                  .order_by(Service.sort, Service.label))] + [("default", DEFAULT_AI)]


def _filter_level(stmt: Select, v: str) -> Select:
    return stmt.where(Job.ai_tier.is_(None)) if v == "default" else stmt.where(Job.ai_tier == v)


AI_USAGE = Report(
    name="ai_usage", label="AI usage",
    description="What each AI level does and costs: documents, calls, failed calls, tokens and time, by level, kind of "
                "document and model, over time. Documents sent without an AI level, on the engine's own default AI, "
                "are counted too.",
    base=lambda ctx: (select().select_from(Job).outerjoin(_TierService, _TierService.c.key == Job.ai_tier)
                      .where(Job.status == "completed", _used_ai, Job.finished_at.is_not(None))),
    cols=[], cols_fn=_ai_usage_cols, group_by=["period", "level", "writer_model", "review_model"],
    group_col=Job.group_id, date_col=Job.finished_at, search=[],
    filters=[PERIOD_FILTER,
             Filter("level", "AI level", _filter_level, _ai_levels),
             Filter("audit", "Audit only", _yes_no(Job.report_only), YES_NO)],
    sort="-period", headline=_ai_usage_headline,
    chart=Chart(x="period", kind="bar", series="level", measures=[
        Measure("ai_usd", "AI cost", "$", num="ai_usd"), Measure("calls", "AI calls", "", num="calls"),
        Measure("tokens", "Tokens", "", num="tokens"), Measure("documents", "Documents", "", num="documents")]),
)


# --- daily use against each service's daily limit (super admins) -----------------------------------

_charged = func.jsonb_array_elements_text(Job.charged_services).table_valued(column("value", String)).lateral("charged")
_DayService = Service.__table__.alias("day_service")
_day = func.date_trunc("day", func.timezone("UTC", Job.submitted_at))


def _daily_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.max(sub.c.pages), func.count().filter(sub.c.pct >= 100))).one()
    return [{"label": "Busiest day", "value": int(row[0] or 0), "hint": "pages, one service"},
            {"label": "Days at the limit", "value": row[1]}]


DAILY = Report(
    name="daily", label="Daily use", description="Pages sent per day (UTC) for each service, against its daily limit.",
    base=lambda ctx: (select().select_from(Job).join(_charged, sa_true())
                      .join(_DayService, _DayService.c.key == _charged.c.value).where(Job.submitted_at.is_not(None))),
    cols=[
        Col("day", "Day (UTC)", _day, "period"),
        Col("service", "Service", _DayService.c.label),
        Col("pages", "Pages sent", func.sum(Job.pages_charged), "num", total=True),
        Col("cap", "Daily limit", func.max(_DayService.c.daily_cap_pages), "num"),
        Col("pct", "Of the limit", func.round(cast(100.0 * func.sum(Job.pages_charged)
                                                    / func.nullif(func.max(_DayService.c.daily_cap_pages), 0), Numeric), 0), "pct"),
    ],
    group_by=["day", "service"],
    group_col=Job.group_id, date_col=Job.submitted_at, search=[],
    filters=[Filter("service", "Service", _eq(_DayService.c.key), _services)],
    sort="-day", headline=_daily_headline, super_only=True,
    chart=Chart(x="day", kind="bar", series="service", ref="cap", measures=[Measure("pages", "Pages sent", "", num="pages")]),
)

# --- scheduled email reports sent (super admins) -------------------------------------------------------

_delivery = func.coalesce(MailOutbox.status, ReportSend.status)

REPORT_EMAILS = Report(
    name="report_emails", label="Report emails",
    description="Scheduled email reports: who got which, for what period, and whether they were delivered.",
    base=lambda ctx: select().select_from(ReportSend).outerjoin(MailOutbox, MailOutbox.id == ReportSend.outbox_id)
    .outerjoin(User, User.id == ReportSend.user_id).outerjoin(Group, Group.id == User.group_id),
    cols=[
        Col("created_at", "When", ReportSend.created_at, "date"),
        Col("person", "To", ReportSend.email, link="person"),
        Col("group", "Group", Group.name, link="group"),
        Col("frequency", "How often", ReportSend.frequency),
        Col("period_start", "Period from", ReportSend.period_start, "date"),
        Col("reports", "Reports", cast(ReportSend.kinds, String)),
        Col("delivery", "Delivery", _delivery),
        Col("error", "Problem", MailOutbox.last_error, default=False),
    ],
    group_col=User.group_id, date_col=ReportSend.created_at, search=[ReportSend.email],
    filters=[
        Filter("frequency", "How often", _eq(ReportSend.frequency), [("monthly", "monthly"), ("weekly", "weekly"),
                                                                     ("daily", "daily")]),
        Filter("delivery", "Delivery", lambda st, v: st.where(_delivery == v),
               [("sent", "sent"), ("queued", "queued"), ("failed", "failed"), ("skipped", "skipped: nothing to report")]),
    ],
    sort="-created_at", links={"group_id": User.group_id}, super_only=True, search_hint="email address",
)

# --- limits hit (super admins): which limits documents reach, and by how much ------------------------

_limit = func.jsonb_array_elements(_limits_json).table_valued(column("value", JSONB)).lateral("limit")


def _limits_headline(db: Session, sub) -> list[dict]:
    top = db.execute(select(sub.c.setting, sub.c.documents).order_by(sub.c.documents.desc()).limit(1)).first()
    n = db.scalar(select(func.count()).select_from(sub)) or 0
    return [{"label": "Limits reached", "value": n},
            {"label": "Most often", "value": top[0] if top else "–", "hint": f"{top[1]} documents" if top else None}]


LIMITS = Report(
    name="limits", label="Limits hit",
    description="Which limits documents reached, how often, and how far past them they would have gone: whether a "
                "limit needs raising. Documents that failed for time or memory are in the Documents report "
                "(filter: Hit a limit).",
    base=lambda ctx: select().select_from(Job).join(_limit, sa_true()),
    cols=[
        Col("setting", "Limit (setting)", _limit.c.value["setting"].astext),
        Col("effect", "What happens past it", func.max(_limit.c.value["effect"].astext)),
        Col("value", "Set to", func.max(cast(_limit.c.value["value"].astext, Integer)), "num"),
        Col("documents", "Documents", func.count(distinct(Job.id)), "num", total=True),
        Col("wanted", "Most wanted", func.max(cast(_limit.c.value["wanted"].astext, Integer)), "num"),
        Col("last", "Last", func.max(Job.finished_at), "date"),
    ],
    group_by=["setting"],
    group_col=Job.group_id, date_col=Job.finished_at,
    search=[_limit.c.value["setting"].astext],
    filters=[Filter("service", "Service or AI level",
                    lambda st, v: st.where(or_(Job.processing == v, Job.ai_tier == v)), _services)],
    sort="-documents", headline=_limits_headline, search_hint="setting", super_only=True,
    chart=Chart(x="setting", kind="hbar", measures=[Measure("documents", "Documents", "", num="documents")]),
)


# --- Describe with AI (super admins): how much it's used and what it costs, to size recharging (#318) ------

_describe_raw = Job.cost["describe_log"]
_describe = func.jsonb_array_elements(
    case((func.jsonb_typeof(_describe_raw) == "array", _describe_raw), else_=func.jsonb_build_array())) \
    .table_valued(column("value", JSONB)).lateral("described")
_described_at = cast(_describe.c.value["at"].astext, DateTime(timezone=True))
_DescribedBy = User.__table__.alias("described_by")


def _describe_cols(ctx: Ctx) -> list[Col]:
    return [
        Col("period", "Period", _period(ctx, _described_at), "period"),
        Col("group", "Group", Group.name),
        Col("person", "Person", _DescribedBy.c.email),
        Col("level", "AI level", _level_expr()),
        Col("uses", "Uses", func.count(), "num", total=True),
        Col("documents", "Documents", func.count(distinct(Job.id)), "num", total=True),
        Col("usd", "Cost ($)", func.coalesce(func.sum(cast(_describe.c.value["usd"].astext, Float)), 0), "money",
            total=True),
    ]


def _describe_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.coalesce(func.sum(sub.c.uses), 0), func.coalesce(func.sum(sub.c.usd), 0),
                            func.count(distinct(sub.c.person)))).one()
    per = float(row[1]) / int(row[0]) if row[0] else 0
    return [{"label": "Uses", "value": int(row[0])}, {"label": "Cost", "value": f"${float(row[1]):,.2f}"},
            {"label": "People", "value": int(row[2])}, {"label": "Per use", "value": f"${per:,.4f}"}]


DESCRIBE = Report(
    name="describe", label="Describe with AI",
    description="How often people use Describe with AI in the tag manager, and what it costs, by group, person and AI "
                "level, over time: to size recharging it. Each use is counted when it happens.",
    base=lambda ctx: (_with_options(select().select_from(Job).join(_describe, sa_true())
                                    .outerjoin(Group, Group.id == Job.group_id)
                                    .outerjoin(_DescribedBy, cast(_DescribedBy.c.id, String) == _describe.c.value["user"].astext))),
    cols=[], cols_fn=_describe_cols, group_by=["period", "group", "person", "level"],
    group_col=Job.group_id, date_col=_described_at, search=[_DescribedBy.c.email],
    filters=[PERIOD_FILTER, Filter("service", "AI level", lambda st, v: st.where(Job.ai_tier == v), _ai_levels)],
    sort="-period", headline=_describe_headline, super_only=True, search_hint="person",
    chart=Chart(x="period", kind="bar", measures=[Measure("uses", "Uses", "", num="uses"),
                                                  Measure("usd", "Cost", "$", num="usd")]),
)


REPORTS: dict[str, Report] = {r.name: r for r in (DOCUMENTS, USAGE, LEDGER, SIGN_INS, EMAIL, HELP, AUDIT,
                                                    QUALITY, FAILURES, COST, AI_USAGE, DAILY, REPORT_EMAILS, LIMITS,
                                                    DESCRIBE)}


# --- running a report ------------------------------------------------------------------------------

@dataclass
class Query:
    since: datetime | None = None
    until: datetime | None = None
    q: str = ""
    sort: str = ""
    page: int = 1
    page_size: int = 25
    cols: list[str] | None = None  # None: the defaults
    filters: dict[str, str] = field(default_factory=dict)


def columns(report: Report, ctx: Ctx, is_super: bool) -> list[Col]:
    cols = report.cols_fn(ctx) if report.cols_fn else report.cols
    return [c for c in cols if is_super or not c.super_only]


def _report(name: str, is_super: bool) -> Report:
    report = REPORTS.get(name)
    if report is None or (report.super_only and not is_super):
        raise ApiError(404, "not_found", "Report not found.")
    return report


def available(is_super: bool) -> list[Report]:
    return [r for r in REPORTS.values() if is_super or not r.super_only]


def period_for(query: Query) -> str:
    """The period asked for, or one that suits the date range: hours for a day or two, days up to two months,
    weeks up to a year, months beyond (or with no start date)."""
    by = query.filters.get("by")
    if by in PERIODS:
        return by
    if not query.since:
        return "month"
    span = (query.until or datetime.now(query.since.tzinfo)) - query.since
    return "hour" if span <= timedelta(days=2) else "day" if span <= timedelta(days=62) \
        else "week" if span <= timedelta(days=366) else "month"


def _build(db: Session, report: Report, query: Query, group_ids: list[uuid.UUID] | None, is_super: bool):
    ctx = Ctx(query.since, query.until, period_for(query))
    cols = columns(report, ctx, is_super)
    stmt = report.base(ctx).add_columns(*[c.expr.label(c.key) for c in cols],
                                        *[e.label(f"_{k}") for k, e in report.links.items()])
    if group_ids is not None:
        stmt = stmt.where(report.group_col.in_(group_ids))
        one = _in_group(query, group_ids)
        if one is not None:  # one of them: a parent's report narrowed to a subgroup (#431)
            stmt = stmt.where(report.group_col == one)
    if report.date_col is not None:
        if query.since:
            stmt = stmt.where(report.date_col >= query.since)
        if query.until:
            stmt = stmt.where(report.date_col < query.until)
    known = {f.key: f for f in report.filters}
    for key, value in query.filters.items():
        if key in known and value:
            stmt = known[key].apply(stmt, value)
    if query.q.strip():
        like = f"%{query.q.strip().lower().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(*[func.lower(e).like(like, escape="\\") for e in report.search]))
    if report.group_by:
        # By output column name: the same expression written twice binds its parameters twice, and Postgres
        # then doesn't see the GROUP BY and the SELECT as the same expression
        present = {c.key for c in cols}  # a super-admin-only column (a model) isn't there for group admins
        stmt = stmt.group_by(*[literal_column(f'"{k}"') for k in report.group_by if k in present])
    return stmt, cols


def _order_by(report: Report, cols: list[Col], sort: str) -> list:
    by = {c.key: c for c in cols}
    key = sort or report.sort
    desc = key.startswith("-")
    col = by.get(key.lstrip("-")) or by.get(report.sort.lstrip("-")) or cols[0]
    target = literal_column(f'"{col.key}"') if report.group_by else col.expr  # grouped: by output column name
    order = [target.desc().nulls_last() if desc else target.asc().nulls_last()]
    if not report.group_by and "job_id" in report.links:
        order.append(report.links["job_id"])  # the same order on every page, and for a document's Previous and Next
    return order


def _order(stmt: Select, report: Report, cols: list[Col], sort: str) -> Select:
    return stmt.order_by(*_order_by(report, cols, sort))


def document_order(db: Session, name: str, query: Query, group_ids: list[uuid.UUID] | None, is_super: bool
                   ) -> tuple[Select, list, ColumnElement, ColumnElement]:
    """A report that lists documents, as the query, order, document id and file name of a document's Previous and
    Next: the rows as the table shows them (the same filters, search and sort), however many pages."""
    report = _report(name, is_super)
    named = next((c for c in report.cols if c.link == "job"), None)
    if report.group_by or named is None or "job_id" not in report.links:
        raise ApiError(404, "not_found", "This report doesn't list documents.")
    stmt, cols = _build(db, report, query, group_ids, is_super)
    return stmt, _order_by(report, cols, query.sort), report.links["job_id"], named.expr


def _shown(cols: list[Col], query: Query) -> list[Col]:
    return [c for c in cols if (c.default if query.cols is None else c.key in query.cols)] or cols


def _value(v):
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, uuid.UUID):
        return str(v)
    return _num(v)


def _chart(db: Session, report: Report, cols: list[Col], sub) -> dict | None:
    ch = report.chart
    if ch is None:
        return None
    kinds = {c.key: c for c in cols}
    keys = [ch.x] + ([ch.series] if ch.series else [])
    measures = []
    for m in ch.measures:
        if m.num not in kinds or (m.den and m.den not in kinds):
            continue  # a super-admin-only measure (cost), for a group admin

        total = func.sum(sub.c[m.num])
        measures.append((m, (total * m.scale / func.nullif(func.sum(sub.c[m.den]), 0)) if m.den else total))
    if not measures:
        return None
    extra = [func.max(sub.c[ch.ref]).label("_ref")] if ch.ref else []
    stmt = select(*[sub.c[k] for k in keys], *[e.label(m.key) for m, e in measures], *extra).group_by(*[sub.c[k] for k in keys])
    if ch.kind == "hbar":
        stmt = stmt.order_by(measures[0][1].desc().nulls_last()).limit(ch.limit)
    else:
        stmt = stmt.order_by(sub.c[ch.x]).limit(2000)
    points = [{k: _value(v) for k, v in r.items()} for r in db.execute(stmt).mappings()]
    return {"kind": ch.kind, "x": ch.x, "x_label": kinds[ch.x].label, "x_kind": kinds[ch.x].kind,
            "series": ch.series, "series_label": kinds[ch.series].label if ch.series else None, "ref": bool(ch.ref),
            "ref_label": kinds[ch.ref].label if ch.ref else None,
            "measures": [{"key": m.key, "label": m.label, "unit": m.unit} for m, _ in measures], "points": points}


IN_GROUP = "in_group"


def _in_group(query: Query, group_ids: list[uuid.UUID]) -> uuid.UUID | None:
    """The group a report covering several was narrowed to, when it's one of them."""
    value = query.filters.get(IN_GROUP)
    return next((g for g in group_ids if str(g) == value), None) if value and len(group_ids) > 1 else None


def _group_filter(db: Session, group_ids: list[uuid.UUID] | None) -> list[dict]:
    """A report covering several groups (a parent and its subgroups, #431; or the groups someone administers) can
    be narrowed to one of them; it's offered first."""
    if group_ids is None or len(group_ids) < 2:
        return []
    names = db.execute(select(Group.id, Group.name, Group.parent_id).where(Group.id.in_(group_ids))).all()
    by_id = {i: n for i, n, _ in names}
    # Each parent, then its subgroups under it
    order = sorted(names, key=lambda r: ((by_id.get(r[2]) or r[1]).lower(), r[2] is not None, r[1].lower()))
    return [{"key": IN_GROUP, "label": "Group",
             "options": [{"value": str(i), "label": f"{n} (subgroup)" if pid in by_id else n} for i, n, pid in order]}]


def run(db: Session, name: str, query: Query, group_ids: list[uuid.UUID] | None, is_super: bool) -> dict:
    report = _report(name, is_super)
    stmt, cols = _build(db, report, query, group_ids, is_super)
    sub = stmt.subquery()
    total_rows = db.scalar(select(func.count()).select_from(sub)) or 0
    totals = {}
    summed = [c for c in cols if c.total]
    if summed:
        row = db.execute(select(*[func.coalesce(func.sum(sub.c[c.key]), 0) for c in summed])).one()
        totals = {c.key: _num(v) for c, v in zip(summed, row)}
    size = max(1, min(query.page_size, MAX_PAGE))
    page = max(1, query.page)
    rows = db.execute(_order(stmt, report, cols, query.sort).limit(size).offset((page - 1) * size)).mappings().all()
    shown = {c.key for c in _shown(cols, query)}
    return {
        "name": report.name, "label": report.label, "description": report.description, "search_hint": report.search_hint,
        "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "total": c.total, "link": c.link,
                     "shown": c.key in shown} for c in cols],
        "rows": [{k: _value(v) for k, v in r.items()} for r in rows],
        "total_rows": total_rows, "page": page, "page_size": size, "totals": totals,
        "headline": report.headline(db, sub) if report.headline else [],
        "chart": _chart(db, report, cols, sub),
        "period": period_for(query) if any(f.key == "by" for f in report.filters) else None,
        "sort": query.sort or report.sort,
        "filters": _group_filter(db, group_ids) + [{"key": f.key, "label": f.label,
                     "options": [{"value": v, "label": lbl} for v, lbl in (f.options(db) if callable(f.options) else f.options)]}
                    for f in report.filters],
    }


def export(db: Session, name: str, query: Query, group_ids: list[uuid.UUID] | None, is_super: bool
           ) -> tuple[list[str], list[list], str]:
    """The rows as shown: the same filters, search, sort and chosen columns, every page."""
    report = _report(name, is_super)
    stmt, cols = _build(db, report, query, group_ids, is_super)
    shown = _shown(cols, query)
    rows = db.execute(_order(stmt, report, cols, query.sort).limit(MAX_EXPORT)).mappings().all()
    out = [[_value(r[c.key]) for c in shown] for r in rows]
    return [c.label for c in shown], out, report.label
