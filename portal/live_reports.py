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

from sqlalchemy import Float, Integer, Numeric, Select, String, Text, and_, case, cast, distinct, func, or_, select
from sqlalchemy import column, literal_column
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from portal.errors import ApiError
from portal.models import (
    AiKind, AuditEvent, Group, GroupService, HelpRequest, Job, MailOutbox, QuotaLedger, ReportSend, Service, User, UserService,
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
    super_only: bool = False
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
    super_only: bool = False
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
            {"label": "Pass PDF/UA-1", "value": f"{rate}%" if rate is not None else "–", "hint": "of finished, remediated documents"},
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
        Col("compliant", "Passes PDF/UA-1", _doc_compliant, "bool"),
        Col("failed_checks", "Failed checks after", cast(_summary["failed_checks_after"].as_string(), Integer), "num", default=False),
        Col("findings", "Review findings", _doc_findings, "num", total=True),
        Col("seconds", "Time to process (s)", func.round(cast(func.extract("epoch", Job.finished_at - Job.submitted_at), Float)),
            "num", default=False),
        Col("external", "Sent elsewhere", func.jsonb_array_length(Job.external_services) > 0, "bool", default=False),
        Col("ai_usd", "AI cost ($)", cast(Job.cost["ai_usd"].as_string(), Float), "money", total=True, super_only=True,
            default=False),
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
            [("pass", "Passes PDF/UA-1"), ("fail", "Doesn't pass"), ("flagged", "Has review findings")]),
        Filter("report_only", "Audit only", _yes_no(Job.report_only), YES_NO),
        Filter("external", "Sent elsewhere", lambda st, v: st.where(
            (func.jsonb_array_length(Job.external_services) > 0) if v == "yes" else (func.jsonb_array_length(Job.external_services) == 0)), YES_NO),
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
    base=lambda ctx: (select().select_from(QuotaLedger).outerjoin(Group, Group.id == QuotaLedger.group_id)
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
    group_col=QuotaLedger.group_id, date_col=QuotaLedger.created_at,
    search=[QuotaLedger.ticket_ref, QuotaLedger.comment, _Person.c.email, QuotaLedger.actor_email],
    filters=[
        Filter("kind", "Entry", _eq(QuotaLedger.kind), [("topup", "Top-up"), ("adjust", "Pool correction"),
                                                         ("reserve", "Held for a document"), ("commit", "Used"),
                                                         ("release", "Released"), ("correct", "Usage correction")]),
        Filter("service", "Service", _eq(QuotaLedger.service_key), _services),
        Filter("override", "Override", _yes_no(QuotaLedger.override), YES_NO),
    ],
    sort="-created_at", links={"group_id": QuotaLedger.group_id, "job_id": QuotaLedger.job_id, "user_id": QuotaLedger.user_id},
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
    search=[AuditEvent.actor_email, AuditEvent.action, cast(AuditEvent.details, Text)],
    filters=[
        Filter("event", "Event", lambda st, v: st.where(AuditEvent.action.like(f"{v.replace('%', '')}%")),
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
        Col("passed", "Pass PDF/UA-1", passed, "num", total=True),
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
    return [{"label": "Pass PDF/UA-1", "value": rate}, {"label": "Remediated documents", "value": int(row[0])},
            {"label": "With review findings", "value": int(row[2])}]


QUALITY = Report(
    name="quality", label="Quality", description="How often remediated documents pass PDF/UA-1, by service and AI level, over time.",
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

_failure = func.jsonb_array_elements(func.coalesce(Job.result_summary["remaining_failures"], cast("[]", JSONB))) \
    .table_valued(column("value", JSONB)).lateral("failure")


def _failure_headline(db: Session, sub) -> list[dict]:
    row = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.checks), 0))).one()
    top = db.execute(select(sub.c.clause, sub.c.documents).order_by(sub.c.documents.desc()).limit(1)).first()
    return [{"label": "Rules still failing", "value": row[0]}, {"label": "Failed checks", "value": int(row[1])},
            {"label": "Most common", "value": top[0] if top else "–", "hint": f"{top[1]} documents" if top else None}]


FAILURES = Report(
    name="failures", label="Remaining failures",
    description="The PDF/UA-1 rules remediated documents still fail, most common first: where the engine falls short.",
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

REPORTS: dict[str, Report] = {r.name: r for r in (DOCUMENTS, USAGE, LEDGER, SIGN_INS, HELP, AUDIT,
                                                    QUALITY, FAILURES, COST, DAILY, REPORT_EMAILS)}


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
        stmt = stmt.group_by(*[literal_column(f'"{k}"') for k in report.group_by])
    return stmt, cols


def _order(stmt: Select, report: Report, cols: list[Col], sort: str) -> Select:
    by = {c.key: c for c in cols}
    key = sort or report.sort
    desc = key.startswith("-")
    col = by.get(key.lstrip("-")) or by.get(report.sort.lstrip("-")) or cols[0]
    target = literal_column(f'"{col.key}"') if report.group_by else col.expr  # grouped: by output column name
    expr = target.desc().nulls_last() if desc else target.asc().nulls_last()
    return stmt.order_by(expr)


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
        total = func.sum(sub.c[m.num])
        measures.append((m, (total * m.scale / func.nullif(func.sum(sub.c[m.den]), 0)) if m.den else total))
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
        "filters": [{"key": f.key, "label": f.label,
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
