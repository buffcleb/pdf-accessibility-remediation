"""Group jobs, ledger and CSV exports for group admins and super admins. Every export is audited."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from portal import audit, live_reports, services, settings_store
from portal.db import get_db
from portal.deps import Principal, current_principal, group_for_admin, require_any_admin, require_super_admin
from portal.errors import not_found
from portal.models import AuditEvent, Group, Job, QuotaLedger, User
from portal.routers.common import LedgerOut
from portal.routers.jobs import DocumentsPage, JobOut, documents_page, job_out
from portal.schemas import effective_cap, group_balance
from portal.security.ratelimit import per_person

router = APIRouter(tags=["reports"])


def _csv(rows: list[list], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        # Neutralize spreadsheet formulas in user-controlled text (CSV injection)
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
                    for v in row])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}-{stamp}.csv"'})


def _period(stmt: Select, col, since: datetime | None, until: datetime | None) -> Select:
    if since:
        stmt = stmt.where(col >= since)
    if until:
        stmt = stmt.where(col < until)
    return stmt


def _iso(d: datetime | None) -> str:
    return d.isoformat(timespec="seconds") if d else ""


# --- group views ---------------------------------------------------------------------------------

@router.get("/groups/{group_id}/jobs", response_model=list[JobOut])
def group_jobs(group_id: uuid.UUID, status: str | None = Query(None, max_length=16), user_id: uuid.UUID | None = None,
               limit: int = Query(100, ge=1, le=1000), before: datetime | None = None,
               p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    stmt = select(Job).where(Job.group_id == group.id, Job.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Job.status == status)
    if user_id:
        stmt = stmt.where(Job.user_id == user_id)
    if before:
        stmt = stmt.where(Job.created_at < before)
    runtime = settings_store.load(db)
    # Cost is for super admins only: what groups are charged isn't the per-page cost
    return [job_out(j, runtime, with_user=True, show_cost=p.is_super_admin)
            for j in db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit))]


@router.get("/groups/{group_id}/documents", response_model=DocumentsPage)
def group_documents(group_id: uuid.UUID, q: str = Query("", max_length=200), status: str | None = Query(None, max_length=20),
                    result: str | None = Query(None, max_length=20), service: str | None = Query(None, max_length=64),
                    audit: bool | None = None, since: datetime | None = None, until: datetime | None = None,
                    sort: str = Query("-created_at", max_length=20), page: int = Query(1, ge=1, le=100_000),
                    page_size: int = Query(25, ge=1, le=200),
                    p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """A group's documents for its admins, a page at a time, searched, filtered and sorted like a person's own
    list; the search also finds a person by email. A group with thousands of documents showed only its newest 200."""
    group = group_for_admin(group_id, p, db)
    # Cost is for super admins only: what groups are charged isn't the per-page cost
    return documents_page(db, [Job.group_id == group.id], q=q, status=status, result=result, service=service,
                          audit=audit, since=since, until=until, sort=sort, page=page, page_size=page_size,
                          people=True, show_cost=p.is_super_admin)


@router.get("/groups/{group_id}/ledger", response_model=list[LedgerOut])
def group_ledger(group_id: uuid.UUID, kind: str | None = Query(None, max_length=16), before_id: int | None = None,
                 limit: int = Query(100, ge=1, le=1000), p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    stmt = select(QuotaLedger).where(QuotaLedger.group_id == group.id)
    if kind:
        stmt = stmt.where(QuotaLedger.kind == kind)
    if before_id:
        stmt = stmt.where(QuotaLedger.id < before_id)
    return [LedgerOut.model_validate(e, from_attributes=True)
            for e in db.scalars(stmt.order_by(QuotaLedger.id.desc()).limit(limit))]


# --- CSV exports ---------------------------------------------------------------------------------

def _usage_rows(db: Session, group_ids: list[uuid.UUID] | None, since, until) -> list[list]:
    """Per user and service they have used or can use: cap, all-time usage, and jobs/pages in the period."""
    job_filter = [Job.status == "completed"]
    if since:
        job_filter.append(Job.finished_at >= since)
    if until:
        job_filter.append(Job.finished_at < until)
    period: dict[tuple, tuple[int, int]] = {}
    for job in db.scalars(select(Job).where(*job_filter)):
        for key in job.charged_services or []:
            jobs, pages = period.get((job.user_id, key), (0, 0))
            period[(job.user_id, key)] = (jobs + 1, pages + job.pages_charged)
    stmt = select(User).order_by(User.email)
    if group_ids is not None:
        stmt = stmt.where(User.group_id.in_(group_ids))
    all_services = services.all_services(db)
    rows = []
    for u in db.scalars(stmt):
        for s in all_services:
            us = u.service(s.key)
            jobs, pages = period.get((u.id, s.key), (0, 0))
            if not (us or jobs or (u.group and services.granted(u, s.key))):
                continue
            cap = effective_cap(u, s.key) if u.group else None
            rows.append([u.group.name if u.group else "", u.email, u.name or "", u.status, u.approval,
                         _iso(u.approval_at), u.approval_by or "", s.key,
                         "unlimited" if cap is None else cap, us.used_pages if us else 0,
                         us.reserved_pages if us else 0, jobs, pages, _iso(u.last_login_at)])
    return rows


USAGE_HEADER = ["group", "email", "name", "status", "approval", "approval_date", "approved_by", "service", "cap_pages", "used_pages_all_time", "reserved_pages",
                "jobs_completed_in_period", "pages_charged_in_period", "last_sign_in"]
LEDGER_HEADER = ["id", "time", "service", "scope", "group", "kind", "pages", "user", "job_id", "ticket", "ticket_url",
                 "override", "comment", "by", "pool_after", "used_after", "reserved_after"]
ACCESS_HEADER = ["time", "group", "email", "event", "outcome", "ip", "user_agent"]


def _ledger_rows(db: Session, group_ids, since, until) -> list[list]:
    stmt = _period(select(QuotaLedger, Group.name).outerjoin(Group, Group.id == QuotaLedger.group_id),
                   QuotaLedger.created_at, since, until)
    if group_ids is not None:
        stmt = stmt.where(QuotaLedger.group_id.in_(group_ids))
    emails = dict(db.execute(select(User.id, User.email)).all())
    return [[e.id, _iso(e.created_at), e.service_key, e.scope, gname or "", e.kind, e.pages,
             emails.get(e.user_id, ""), e.job_id or "", e.ticket_ref or "", e.ticket_url or "",
             "yes" if e.override else "", e.comment or "", e.actor_email or "", e.pool_after, e.used_after,
             e.reserved_after]
            for e, gname in db.execute(stmt.order_by(QuotaLedger.id))]


def _access_rows(db: Session, group_ids, since, until) -> list[list]:
    stmt = _period(select(AuditEvent).where(AuditEvent.action.in_(("login", "logout", "logout_all",
                                                                    "login.code_requested", "session.revoked"))),
                   AuditEvent.created_at, since, until)
    if group_ids is not None:
        stmt = stmt.where(AuditEvent.group_id.in_(group_ids))
    names = dict(db.execute(select(Group.id, Group.name)).all())
    return [[_iso(e.created_at), names.get(e.group_id, ""), e.actor_email or "", e.action, e.outcome,
             str(e.ip or ""), e.user_agent or ""] for e in db.scalars(stmt.order_by(AuditEvent.id))]


REPORTS = {
    "usage": (USAGE_HEADER, _usage_rows),
    "ledger": (LEDGER_HEADER, _ledger_rows),
    "access": (ACCESS_HEADER, _access_rows),
}


@router.get("/groups/{group_id}/reports/{name}.csv", dependencies=[Depends(per_person("export"))])
def group_report(group_id: uuid.UUID, name: str, request: Request, since: datetime | None = None,
                 until: datetime | None = None, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    group = group_for_admin(group_id, p, db)
    if name not in REPORTS:
        raise not_found("Report")
    header, build = REPORTS[name]
    rows = build(db, [group.id], since, until)
    audit.record(db, request, "report.exported", actor=p.user, target_type="group", target_id=group.id,
                 group_id=group.id, details={"report": name, "rows": len(rows), "since": _iso(since),
                                             "until": _iso(until)})
    db.commit()
    return _csv(rows, header, f"{name}-{group.name.lower().replace(' ', '-')}")


@router.get("/admin/reports/{name}.csv", dependencies=[Depends(per_person("export"))])
def all_report(name: str, request: Request, since: datetime | None = None, until: datetime | None = None,
               p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    if name == "groups":
        rows = []
        for g in db.scalars(select(Group).order_by(Group.name)):
            members = db.scalar(select(func.count()).select_from(User).where(User.group_id == g.id))
            for gs in sorted(g.services, key=lambda x: x.service_key):
                b = group_balance(g, gs.service_key)
                topups = db.execute(select(func.count(), func.coalesce(func.sum(QuotaLedger.pages), 0),
                                           func.count().filter(QuotaLedger.override))
                                    .where(QuotaLedger.group_id == g.id, QuotaLedger.kind == "topup",
                                           QuotaLedger.service_key == gs.service_key)).one()
                rows.append([g.name, g.status, "yes" if g.local_only else "", members, gs.service_key,
                             "yes" if gs.enabled else "", b.pool_pages, b.used_pages, b.reserved_pages,
                             b.remaining_pages, topups[0], topups[1], topups[2]])
        header = ["group", "status", "local_only", "users", "service", "enabled", "pool_pages", "used_pages",
                  "reserved_pages", "remaining_pages", "topups", "pages_topped_up", "override_topups"]
    elif name in REPORTS:
        header, build = REPORTS[name]
        rows = build(db, None, since, until)
    else:
        raise not_found("Report")
    audit.record(db, request, "report.exported", actor=p.user, details={
        "report": name, "scope": "all groups", "rows": len(rows), "since": _iso(since), "until": _iso(until)})
    db.commit()
    return _csv(rows, header, f"{name}-all-groups")


# --- live reports --------------------------------------------------------------------------------

def _live_scope(p: Principal, db: Session, group_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    """Super admins: every group, or the one asked for. Group admins: their groups, or one of them."""
    if group_id is not None:
        return [group_for_admin(group_id, p, db).id]
    if p.is_super_admin:
        return None
    ids = sorted(p.user.admin_group_ids(), key=str)
    if not ids:
        raise not_found("Report")
    return ids


def _live_query(request: Request, since: datetime | None, until: datetime | None, q: str, sort: str, page: int,
                page_size: int, cols: str | None) -> live_reports.Query:
    reserved = {"since", "until", "q", "sort", "page", "page_size", "cols", "group_id"}
    filters = {k: v[:200] for k, v in request.query_params.items() if k not in reserved}
    return live_reports.Query(since=since, until=until, q=q[:200], sort=sort[:64], page=page, page_size=page_size,
                              cols=[c for c in cols.split(",") if c] if cols is not None else None, filters=filters)


@router.get("/reports")
def list_live_reports(p: Principal = Depends(require_any_admin)):
    return [{"name": r.name, "label": r.label, "description": r.description}
            for r in live_reports.available(p.is_super_admin)]


@router.get("/reports/{name}.csv", dependencies=[Depends(per_person("export"))])
def export_live_report(name: str, request: Request, since: datetime | None = None, until: datetime | None = None,
                       q: str = "", sort: str = "", cols: str | None = Query(None, max_length=2000),
                       group_id: uuid.UUID | None = None, p: Principal = Depends(require_any_admin),
                       db: Session = Depends(get_db)):
    """The report as shown in the app: the same filters, search, sort and columns. Audited."""
    scope = _live_scope(p, db, group_id)
    query = _live_query(request, since, until, q, sort, 1, live_reports.MAX_PAGE, cols)
    header, rows, label = live_reports.export(db, name, query, scope, p.is_super_admin)
    audit.record(db, request, "report.exported", actor=p.user, target_type="group" if group_id else None,
                 target_id=group_id, group_id=group_id,
                 details={"report": name, "rows": len(rows), "since": _iso(since), "until": _iso(until),
                          "search": query.q or None, "filters": query.filters or None,
                          "scope": "one group" if group_id else ("all groups" if scope is None else "their groups")})
    db.commit()
    return _csv(rows, header, label.lower().replace(" ", "-"))


@router.get("/reports/{name}")
def live_report(name: str, request: Request, since: datetime | None = None, until: datetime | None = None,
                q: str = "", sort: str = "", page: int = Query(1, ge=1, le=100_000),
                page_size: int = Query(25, ge=1, le=live_reports.MAX_PAGE), cols: str | None = Query(None, max_length=2000),
                group_id: uuid.UUID | None = None, p: Principal = Depends(require_any_admin), db: Session = Depends(get_db)):
    """One page of a report, with totals and headline numbers over every matching row. Viewing isn't audited."""
    scope = _live_scope(p, db, group_id)
    return live_reports.run(db, name, _live_query(request, since, until, q, sort, page, page_size, cols), scope,
                            p.is_super_admin)
