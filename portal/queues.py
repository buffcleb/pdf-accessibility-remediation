"""Everything that waits its turn, in one place for super admins: documents (one queue per processing service
and AI level), saved edits, AI reviews of saved versions, and outgoing email.

Each item can be acted on: a waiting one canceled, one in progress stopped, a failed one tried again or removed
from the list.

Pausing a queue lets work already started finish and starts nothing new until it's resumed. Documents that use a
paused service stay in its queue and wait; they're never moved to another service. Sign-in codes aren't queued
and can't be paused, so nobody can be locked out.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from portal import audit, outbox, quota, settings_store
from portal.errors import ApiError, not_found
from portal.mail import Mail
from portal.models import DocumentVersion, Group, Job, MailOutbox, QueuePause, QueueRemoval, Service, User

EDITS = "edits"
REVIEWS = "reviews"
MAIL = outbox.MAIL_QUEUE
RECENT = timedelta(days=7)
STATES = ("waiting", "active", "failed")
MAX_ITEMS = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def service_key(key: str) -> str:
    return f"service:{key}"


def paused(db: Session) -> dict[str, QueuePause]:
    return {p.key: p for p in db.scalars(select(QueuePause))}


def is_paused(db: Session, key: str) -> bool:
    return db.get(QueuePause, key) is not None


def job_services(job: Job) -> list[str]:
    """The services a document needs: reports only use the standard route."""
    if job.report_only:
        return ["standard"]
    return [job.processing] + ([job.ai_tier] if job.ai_tier else [])


def paused_services(job: Job, pauses: dict[str, QueuePause]) -> list[str]:
    return [k for k in job_services(job) if service_key(k) in pauses]


# --- what there is --------------------------------------------------------------------------------

@dataclass
class QueueDef:
    key: str
    label: str
    kind: str  # documents | edits | reviews | mail
    service: str | None = None


def definitions(db: Session) -> list[QueueDef]:
    services = db.scalars(select(Service).order_by(Service.kind.desc(), Service.sort, Service.label)).all()
    defs = [QueueDef(service_key(s.key), (s.label if s.kind == "processing" else f"AI: {s.label}"), "documents", s.key)
            for s in services]
    return defs + [QueueDef(EDITS, "Saved edits", "edits"), QueueDef(REVIEWS, "AI reviews of saved versions", "reviews"),
                   QueueDef(MAIL, "Email", "mail")]


def _definition(db: Session, key: str) -> QueueDef:
    d = next((d for d in definitions(db) if d.key == key), None)
    if d is None:
        raise not_found("Queue")
    return d


def _job_filter(d: QueueDef):
    return or_(Job.processing == d.service, Job.ai_tier == d.service)


def _removed(kind: str, id_col, failed_at):
    """True for a failed item a super admin took off the list, unless it has failed again since (tried again from
    somewhere else): then it's a new failure to look at."""
    return select(QueueRemoval.item_id).where(QueueRemoval.kind == kind, QueueRemoval.item_id == id_col,
                                              QueueRemoval.removed_at >= func.coalesce(failed_at, QueueRemoval.removed_at)).exists()


_REVIEW_FAILED_AT = func.coalesce(DocumentVersion.reviewed_at, DocumentVersion.created_at)


def _counts(db: Session, d: QueueDef) -> dict:
    since = _now() - RECENT

    def n(stmt) -> int:
        return db.scalar(stmt) or 0

    if d.kind == "documents":
        base = select(func.count()).select_from(Job).where(_job_filter(d))
        oldest = select(func.min(Job.created_at)).where(_job_filter(d), Job.status == "queued")
        return {"waiting": n(base.where(Job.status == "queued")),
                "active": n(base.where(Job.status.in_(("submitted", "processing")))),
                "failed": n(base.where(Job.status == "failed", Job.finished_at > since,
                                       ~_removed("documents", Job.id, Job.finished_at))),
                "oldest_waiting": db.scalar(oldest)}
    if d.kind == "edits":
        base = select(func.count()).select_from(DocumentVersion)
        return {"waiting": n(base.where(DocumentVersion.status == "queued")),
                "active": n(base.where(DocumentVersion.status.in_(("submitted", "processing")))),
                "failed": n(base.where(DocumentVersion.status == "failed", DocumentVersion.finished_at > since,
                                       ~_removed("edits", DocumentVersion.id, DocumentVersion.finished_at))),
                "oldest_waiting": db.scalar(select(func.min(DocumentVersion.created_at))
                                            .where(DocumentVersion.status == "queued"))}
    if d.kind == "reviews":
        base = select(func.count()).select_from(DocumentVersion).where(DocumentVersion.status == "ready")
        return {"waiting": n(base.where(DocumentVersion.review_status == "queued")),
                "active": n(base.where(DocumentVersion.review_status == "running")),
                "failed": n(base.where(DocumentVersion.review_status == "failed", DocumentVersion.created_at > since,
                                       ~_removed("reviews", DocumentVersion.id, _REVIEW_FAILED_AT))),
                "oldest_waiting": db.scalar(select(func.min(DocumentVersion.created_at)).where(
                    DocumentVersion.status == "ready", DocumentVersion.review_status == "queued"))}
    base = select(func.count()).select_from(MailOutbox)
    return {"waiting": n(base.where(MailOutbox.status == "queued")), "active": 0,
            "failed": n(base.where(MailOutbox.status == "failed", MailOutbox.created_at > since,
                                   ~_removed("mail", MailOutbox.id, MailOutbox.finished_at))),
            "sent": n(base.where(MailOutbox.status == "sent", MailOutbox.finished_at > since)),
            "oldest_waiting": db.scalar(select(func.min(MailOutbox.created_at)).where(MailOutbox.status == "queued"))}


def _pause_out(p: QueuePause | None) -> dict | None:
    return {"by": p.paused_by, "at": p.paused_at, "note": p.note} if p else None


def overview(db: Session) -> list[dict]:
    pauses = paused(db)
    out = []
    for d in definitions(db):
        if d.kind == "documents":
            s = db.get(Service, d.service)
            counts = _counts(db, d)
            # Services that are off and have nothing in them are left out
            if s is not None and not s.enabled and not (counts["waiting"] or counts["active"] or counts["failed"]):
                continue
        else:
            counts = _counts(db, d)
        out.append({"key": d.key, "label": d.label, "kind": d.kind, **counts, "paused": _pause_out(pauses.get(d.key))})
    return out


# --- pausing --------------------------------------------------------------------------------------

def pause(db: Session, request: Request | None, key: str, actor: User, note: str | None) -> None:
    d = _definition(db, key)
    if db.get(QueuePause, key) is not None:
        return
    db.add(QueuePause(key=key, paused_by=actor.email, note=(note or "").strip() or None))
    audit.record(db, request, "queue.paused", actor=actor, target_type="queue", target_id=key,
                 details={"queue": d.label, "note": note})


def resume(db: Session, request: Request | None, key: str, actor: User) -> None:
    d = _definition(db, key)
    p = db.get(QueuePause, key)
    if p is None:
        return
    db.delete(p)
    audit.record(db, request, "queue.resumed", actor=actor, target_type="queue", target_id=key,
                 details={"queue": d.label, "paused_by": p.paused_by,
                          "paused_minutes": round((_now() - p.paused_at).total_seconds() / 60)})


# --- items ----------------------------------------------------------------------------------------

def _wait_reason(job: Job, pauses: dict[str, QueuePause], labels: dict[str, str], queue_service: str) -> str:
    keys = paused_services(job, pauses)
    if keys:
        # A document uses its processing service and its AI level, and shows in both queues; say which one holds it
        others = [labels.get(k, k) for k in keys if k != queue_service]
        if not others:
            return "Paused"
        return f"Waiting for {' and '.join(others)}, which {'is' if len(others) == 1 else 'are'} paused"
    stage = (job.progress or {}).get("stage")
    if job.expedited_at:
        return f"Process now (asked by {job.expedited_by}): next when a slot is free"
    if stage == "waiting_large":
        every = (job.progress or {}).get("every")
        return f"Large document: starts after every {every} smaller ones, or when nothing smaller is waiting"
    if stage == "waiting_at_once":
        return "Waiting for a free place: " + ", ".join(labels.get(k, k) for k in (job.progress or {}).get("services") or [])
    if stage == "waiting_daily_cap":
        return "Daily limit reached: " + ", ".join(labels.get(k, k) for k in (job.progress or {}).get("services") or [])
    return "Waiting its turn"


def items(db: Session, key: str, state: str) -> dict:
    if state not in STATES:
        raise ApiError(422, "bad_state", f"State must be one of {', '.join(STATES)}.")
    d = _definition(db, key)
    since = _now() - RECENT
    labels = {s.key: s.label for s in db.scalars(select(Service))}
    pauses = paused(db)
    threshold = settings_store.load(db).large_doc_pages
    rows: list[dict] = []
    if d.kind == "documents":
        where = {"waiting": Job.status == "queued", "active": Job.status.in_(("submitted", "processing")),
                 "failed": (Job.status == "failed") & (Job.finished_at > since)
                 & ~_removed("documents", Job.id, Job.finished_at)}[state]
        order = Job.created_at if state != "failed" else Job.finished_at.desc()
        for j in db.scalars(select(Job).where(_job_filter(d), where).order_by(order).limit(MAX_ITEMS)):
            rows.append({
                "id": str(j.id), "job_id": str(j.id), "title": j.filename, "who": j.user.email, "pages": j.pages,
                "options": " + ".join(labels.get(k, k) for k in job_services(j)),
                "created_at": j.created_at, "started_at": j.submitted_at, "finished_at": j.finished_at,
                "detail": (_wait_reason(j, pauses, labels, d.service) if state == "waiting"
                           else j.error_message if state == "failed" else None),
                "error_code": j.error_code if state == "failed" else None,
                "can_retry": state == "failed" and j.files_deleted_at is None and any(f.kind == "input" for f in j.files),
                "can_cancel": state == "waiting", "can_stop": state == "active", "can_remove": state == "failed",
                "can_process_now": state == "waiting" and 0 < threshold < j.pages and j.expedited_at is None,
            })
    elif d.kind in ("edits", "reviews"):
        if d.kind == "edits":
            where = {"waiting": DocumentVersion.status == "queued",
                     "active": DocumentVersion.status.in_(("submitted", "processing")),
                     "failed": (DocumentVersion.status == "failed") & (DocumentVersion.finished_at > since)
                     & ~_removed("edits", DocumentVersion.id, DocumentVersion.finished_at)}[state]
        else:
            where = (DocumentVersion.status == "ready") & {
                "waiting": DocumentVersion.review_status == "queued",
                "active": DocumentVersion.review_status == "running",
                "failed": (DocumentVersion.review_status == "failed") & (DocumentVersion.created_at > since)
                & ~_removed("reviews", DocumentVersion.id, _REVIEW_FAILED_AT)}[state]
        order = DocumentVersion.created_at if state != "failed" else DocumentVersion.created_at.desc()
        for v in db.scalars(select(DocumentVersion).where(where).order_by(order).limit(MAX_ITEMS)):
            job = v.job
            if d.kind == "edits":
                detail = v.error_message if state == "failed" else None
                started = v.submitted_at
            else:
                tier_paused = job.ai_tier and service_key(job.ai_tier) in pauses
                detail = (v.review_error if state == "failed"
                          else f"Paused: {labels.get(job.ai_tier, job.ai_tier)}" if state == "waiting" and tier_paused
                          else None)
                started = v.review_submitted_at
            rows.append({
                "id": str(v.id), "job_id": str(job.id), "title": f"{job.filename}, version {v.number}",
                "who": (v.created_by_email if d.kind == "edits" else v.review_requested_by) or job.user.email,
                "pages": job.pages, "options": None, "created_at": v.created_at, "started_at": started,
                "finished_at": v.finished_at if d.kind == "edits" else v.reviewed_at,
                "detail": detail, "error_code": v.error_code if d.kind == "edits" and state == "failed" else None,
                "can_retry": state == "failed" and job.files_deleted_at is None,
                "can_cancel": state == "waiting", "can_stop": state == "active", "can_remove": state == "failed",
            })
    else:
        if state == "active":
            return {"queue": d.label, "state": state, "items": []}
        where = MailOutbox.status == "queued" if state == "waiting" else (
            (MailOutbox.status == "failed") & (MailOutbox.created_at > since) & ~_removed("mail", MailOutbox.id, MailOutbox.finished_at))
        order = MailOutbox.created_at if state == "waiting" else MailOutbox.created_at.desc()
        for m in db.scalars(select(MailOutbox).where(where).order_by(order).limit(MAX_ITEMS)):
            rows.append({
                "id": str(m.id), "job_id": None, "title": m.subject, "who": m.to_addr, "pages": None, "options": None,
                "created_at": m.created_at, "started_at": None, "finished_at": m.finished_at,
                "detail": (m.last_error if state == "failed" else
                           "Paused" if MAIL in pauses else
                           f"Trying again after a temporary failure ({m.attempts} so far): {m.last_error}"
                           if m.attempts else "Waiting its turn"),
                "error_code": None, "can_retry": state == "failed", "can_cancel": state == "waiting",
                "can_remove": state == "failed",
            })
    return {"queue": d.label, "state": state, "items": rows}


# --- everything at once, as it happens ------------------------------------------------------------

LIVE_RECENT = timedelta(minutes=10)
LIVE_STATES = ("active", "waiting", "done", "failed", "canceled")
LIVE_MAX = 300  # of each kind: the view is for what is moving now, not for the backlog's whole length


def _like(q: str) -> str:
    return "%" + q.strip().lower().replace("%", r"\%").replace("_", r"\_") + "%"


def live(db: Session, *, q: str = "", group_id: uuid.UUID | None = None, queue: str | None = None,
         state: str | None = None) -> dict:
    """Every queue's items in one list: what is in progress (with the stage it has reached), what is waiting (and
    why), and what finished, failed or was canceled in the last few minutes, so an item doesn't vanish the moment it
    is done. Narrowed by a search (a document's name, a person's address, an email's subject), a group, one queue
    or one state. The per-queue lists show one queue and one state at a time, which doesn't answer "where is this
    person's document now"."""
    if state and state not in LIVE_STATES:
        raise ApiError(422, "bad_state", f"State must be one of {', '.join(LIVE_STATES)}.")
    only = _definition(db, queue) if queue else None
    kinds = {only.kind} if only else {"documents", "edits", "reviews", "mail"}
    since = _now() - LIVE_RECENT
    labels = {s.key: s.label for s in db.scalars(select(Service))}
    groups = {g.id: g.name for g in db.scalars(select(Group))}
    pauses = paused(db)
    threshold = settings_store.load(db).large_doc_pages
    like = _like(q) if q.strip() else None
    by_person = select(User.id).where(func.lower(User.email).like(like, escape="\\")) if like else None
    rows: list[dict] = []
    more = False

    def take(stmt) -> list:
        nonlocal more
        found = db.scalars(stmt.limit(LIVE_MAX + 1)).all()
        more = more or len(found) > LIVE_MAX
        return found[:LIVE_MAX]

    if "documents" in kinds:
        where = [or_(Job.status.in_(("queued", "submitted", "processing")),
                     Job.status.in_(("completed", "failed", "canceled")) & (Job.finished_at > since))]
        if only:
            where.append(_job_filter(only))
        if group_id:
            where.append(Job.group_id == group_id)
        if like:
            where.append(or_(func.lower(Job.filename).like(like, escape="\\"), Job.user_id.in_(by_person)))
        for j in take(select(Job).where(*where).order_by(Job.created_at.desc())):
            st = {"queued": "waiting", "submitted": "active", "processing": "active", "completed": "done"}.get(j.status, j.status)
            progress = j.progress or {}
            rows.append({
                "key": service_key(job_services(j)[0]), "kind": "documents", "id": str(j.id), "job_id": str(j.id),
                "title": j.filename, "who": j.user.email, "group_id": j.group_id, "group": groups.get(j.group_id),
                "queue": " + ".join(labels.get(k, k) for k in job_services(j)), "pages": j.pages, "state": st,
                "stage": progress.get("stage") if st == "active" else None,
                "percent": progress.get("percent") if st == "active" else None,
                "detail": (_wait_reason(j, pauses, labels, "") if st == "waiting" else j.error_message if st == "failed" else None),
                "created_at": j.created_at, "started_at": j.submitted_at, "finished_at": j.finished_at,
                "can_retry": False, "can_cancel": st == "waiting", "can_stop": st == "active", "can_remove": False,
                "can_process_now": st == "waiting" and 0 < threshold < j.pages and j.expedited_at is None,
            })

    def version_rows(kind: str) -> None:
        if kind == "edits":
            state_of = {"queued": "waiting", "submitted": "active", "processing": "active", "ready": "done", "failed": "failed"}
            col, done_at, started = DocumentVersion.status, DocumentVersion.finished_at, DocumentVersion.submitted_at
            where = [or_(col.in_(("queued", "submitted", "processing")),
                         col.in_(("ready", "failed")) & started.is_not(None) & (done_at > since))]
        else:
            state_of = {"queued": "waiting", "running": "active", "done": "done", "failed": "failed"}
            col, done_at, started = DocumentVersion.review_status, DocumentVersion.reviewed_at, DocumentVersion.review_submitted_at
            where = [DocumentVersion.status == "ready",
                     or_(col.in_(("queued", "running")), col.in_(("done", "failed")) & (done_at > since))]
        if group_id:
            where.append(Job.group_id == group_id)
        if like:
            where.append(or_(func.lower(Job.filename).like(like, escape="\\"), Job.user_id.in_(by_person),
                             func.lower(func.coalesce(DocumentVersion.created_by_email, "")).like(like, escape="\\")))
        for v in take(select(DocumentVersion).join(Job, Job.id == DocumentVersion.job_id).where(*where)
                      .order_by(DocumentVersion.created_at.desc())):
            job, st = v.job, state_of.get(getattr(v, "status" if kind == "edits" else "review_status"), "waiting")
            if kind == "edits":
                who, detail = v.created_by_email or job.user.email, v.error_message if st == "failed" else None
                at_start, at_end = v.submitted_at, v.finished_at
            else:
                tier_paused = job.ai_tier and service_key(job.ai_tier) in pauses
                who = v.review_requested_by or job.user.email
                detail = (v.review_error if st == "failed"
                          else f"Paused: {labels.get(job.ai_tier, job.ai_tier)}" if st == "waiting" and tier_paused else None)
                at_start, at_end = v.review_submitted_at, v.reviewed_at
            if st == "waiting" and detail is None and (EDITS if kind == "edits" else REVIEWS) in pauses:
                detail = "Paused"
            rows.append({
                "key": EDITS if kind == "edits" else REVIEWS, "kind": kind, "id": str(v.id), "job_id": str(job.id),
                "title": f"{job.filename}, version {v.number}", "who": who, "group_id": job.group_id,
                "group": groups.get(job.group_id), "queue": "Saved edit" if kind == "edits" else "AI review of a saved version",
                "pages": job.pages, "state": st, "stage": None, "percent": None, "detail": detail,
                "created_at": v.created_at, "started_at": at_start, "finished_at": at_end if st in ("done", "failed") else None,
                "can_retry": False, "can_cancel": st == "waiting", "can_stop": st == "active", "can_remove": False,
                "can_process_now": False,
            })

    for kind in ("edits", "reviews"):
        if kind in kinds:
            version_rows(kind)

    # Email belongs to no group: with a group chosen it is left out, not shown as if it were that group's
    if "mail" in kinds and not group_id:
        where = [or_(MailOutbox.status == "queued", MailOutbox.status.in_(("sent", "failed")) & (MailOutbox.finished_at > since))]
        if like:
            where.append(or_(func.lower(MailOutbox.subject).like(like, escape="\\"),
                             func.lower(MailOutbox.to_addr).like(like, escape="\\")))
        for m in take(select(MailOutbox).where(*where).order_by(MailOutbox.created_at.desc())):
            st = {"queued": "waiting", "sent": "done"}.get(m.status, m.status)
            rows.append({
                "key": MAIL, "kind": "mail", "id": str(m.id), "job_id": None, "title": m.subject, "who": m.to_addr,
                "group_id": None, "group": None, "queue": "Email", "pages": None, "state": st, "stage": None, "percent": None,
                "detail": (m.last_error if st == "failed" else None if st != "waiting" else "Paused" if MAIL in pauses
                           else f"Trying again after a temporary failure ({m.attempts} so far): {m.last_error}" if m.attempts else None),
                "created_at": m.created_at, "started_at": None, "finished_at": m.finished_at,
                "can_retry": False, "can_cancel": st == "waiting", "can_stop": False, "can_remove": False,
                "can_process_now": False,
            })

    counts = {k: sum(1 for r in rows if r["state"] == k) for k in LIVE_STATES}
    if state:
        rows = [r for r in rows if r["state"] == state]
    # What is moving first, then what waits (longest first), then what has just ended (latest first)
    rank = {"active": 0, "waiting": 1}
    ended = lambda r: -(r["finished_at"] or r["created_at"]).timestamp()  # noqa: E731
    rows.sort(key=lambda r: (rank.get(r["state"], 2), r["created_at"].timestamp() if r["state"] in rank else ended(r)))
    return {"items": rows, "counts": counts, "more": more, "recent_minutes": int(LIVE_RECENT.total_seconds() // 60)}


# --- retry and cancel -------------------------------------------------------------------------------

def _uuid(item_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(item_id)
    except ValueError:
        raise not_found("Item") from None


def retry(db: Session, request: Request | None, key: str, item_id: str, actor: User) -> None:
    d = _definition(db, key)
    iid = _uuid(item_id)
    if d.kind == "documents":
        job = db.scalar(select(Job).where(Job.id == iid).with_for_update())
        if job is None or d.service not in job_services(job):
            raise not_found("Document")
        if job.status != "failed":
            raise ApiError(409, "not_failed", "Only failed documents can be tried again.")
        if job.files_deleted_at is not None or not any(f.kind == "input" for f in job.files):
            raise ApiError(410, "files_deleted", "This document's file has been deleted, so it can't be tried again.")
        if not job.report_only:
            # Pages are held again on the person's own limits, as when they first sent it
            keys = job_services(job)
            quota.reserve(db, job.user, job.pages, job.id, keys)
            job.pages_charged, job.charged_services = job.pages, keys
        old = job.error_code
        forget_removal(db, "documents", job.id)
        job.status, job.error_code, job.error_message, job.progress = "queued", None, None, None
        job.engine_job_id, job.submitted_at, job.finished_at, job.submit_attempts = None, None, None, 0
        job.expedited_at, job.expedited_by = None, None
        audit.record(db, request, "job.retried", actor=actor, target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"queue": d.label, "previous_error": old, "pages": job.pages_charged})
    elif d.kind == "edits":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None:
            raise not_found("Saved edit")
        if v.status != "failed":
            raise ApiError(409, "not_failed", "Only failed saves can be tried again.")
        if v.job.files_deleted_at is not None:
            raise ApiError(410, "files_deleted", "This document's files have been deleted, so it can't be tried again.")
        newer = db.scalar(select(func.count()).select_from(DocumentVersion).where(
            DocumentVersion.job_id == v.job_id, DocumentVersion.number > v.number))
        if newer:
            raise ApiError(409, "superseded", "The document has newer versions, so this save can't be tried again.")
        old = v.error_code
        forget_removal(db, "edits", v.id)
        v.status, v.error_code, v.error_message = "queued", None, None
        v.engine_job_id, v.submitted_at, v.finished_at = None, None, None
        audit.record(db, request, "version.retried", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number, "previous_error": old})
    elif d.kind == "reviews":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None or v.status != "ready":
            raise not_found("Review")
        if v.review_status != "failed":
            raise ApiError(409, "not_failed", "Only failed reviews can be tried again.")
        if v.job.files_deleted_at is not None:
            raise ApiError(410, "files_deleted", "This document's files have been deleted, so it can't be reviewed.")
        forget_removal(db, "reviews", v.id)
        v.review_status, v.review_error, v.review_engine_job_id, v.review_submitted_at = "queued", None, None, None
        audit.record(db, request, "version.review_retried", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number})
    else:
        m = db.scalar(select(MailOutbox).where(MailOutbox.id == iid).with_for_update())
        if m is None:
            raise not_found("Email")
        if m.status != "failed":
            raise ApiError(409, "not_failed", "Only failed emails can be tried again.")
        forget_removal(db, "mail", m.id)
        m.status, m.attempts, m.next_try_at, m.finished_at = "queued", 0, _now(), None
        audit.record(db, request, "mail.retried", actor=actor, target_type="mail", target_id=m.id,
                     details={"to": m.to_addr, "subject": m.subject, "previous_error": m.last_error})


def cancel(db: Session, request: Request | None, key: str, item_id: str, actor: User) -> None:
    d = _definition(db, key)
    iid = _uuid(item_id)
    if d.kind == "documents":
        job = db.scalar(select(Job).where(Job.id == iid).with_for_update())
        if job is None or d.service not in job_services(job):
            raise not_found("Document")
        if job.status != "queued":
            raise ApiError(409, "not_waiting", "Only documents still waiting can be canceled; this one has started.")
        quota.release(db, job, "canceled by support")
        job.status, job.progress, job.finished_at = "canceled", None, _now()
        audit.record(db, request, "job.canceled", actor=actor, target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"by_support": True, "queue": d.label})
        rs = settings_store.load(db)
        outbox.queue(db, [Mail(to=job.user.email, subject=f"Canceled: {job.filename}", text=(
            f"Your document \"{job.filename}\" was canceled by support before it was processed. "
            f"No pages were charged.\n\nYou can send it again at any time.\n\n{rs.service_name}\n"))],
            rs.mail_from_name, "job", about=f"job:{job.id}")
    elif d.kind == "edits":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None:
            raise not_found("Saved edit")
        if v.status != "queued":
            raise ApiError(409, "not_waiting", "Only saves still waiting can be canceled; this one has started.")
        v.status, v.error_code, v.finished_at = "failed", "canceled", _now()
        v.error_message = "This save was canceled by support. The previous version is unchanged."
        if v.review_status in ("queued", "running"):
            v.review_status, v.review_error = "failed", "The version wasn't saved, so it wasn't reviewed."
        audit.record(db, request, "version.canceled", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number})
    elif d.kind == "reviews":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None or v.status != "ready":
            raise not_found("Review")
        if v.review_status != "queued":
            raise ApiError(409, "not_waiting", "Only reviews still waiting can be canceled; this one has started.")
        v.review_status, v.review_error = "failed", "This review was canceled by support."
        audit.record(db, request, "version.review_canceled", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number})
    else:
        m = db.scalar(select(MailOutbox).where(MailOutbox.id == iid).with_for_update())
        if m is None:
            raise not_found("Email")
        if m.status != "queued":
            raise ApiError(409, "not_waiting", "Only emails still waiting can be canceled.")
        m.status, m.finished_at = "canceled", _now()
        audit.record(db, request, "mail.canceled", actor=actor, target_type="mail", target_id=m.id,
                     details={"to": m.to_addr, "subject": m.subject})


def _stop_at_engine(engine_job_id: str | None) -> str | None:
    """Ask the engine to stop a job. Returns why it couldn't be asked (the item is stopped here regardless: the
    engine's job then runs to its end unseen, or to its time limit)."""
    if not engine_job_id:
        return None
    from portal.engine import EngineUnavailable, get_engine_client

    try:
        get_engine_client().stop(engine_job_id)
        return None
    except EngineUnavailable as e:
        return str(e)[:200]


def stop(db: Session, request: Request | None, key: str, item_id: str, actor: User) -> None:
    """Stop something in progress: a document being processed, a save being checked, a review being run. The
    engine is told to stop it (freeing its place), and it ends here at once."""
    d = _definition(db, key)
    iid = _uuid(item_id)
    if d.kind == "documents":
        job = db.scalar(select(Job).where(Job.id == iid).with_for_update())
        if job is None or d.service not in job_services(job):
            raise not_found("Document")
        if job.status not in ("submitted", "processing"):
            raise ApiError(409, "not_in_progress", "Only documents in progress can be stopped; this one isn't any more.")
        unreached = _stop_at_engine(job.engine_job_id)
        quota.release(db, job, "stopped by support")
        job.status, job.progress, job.finished_at = "canceled", None, _now()
        audit.record(db, request, "job.stopped", actor=actor, target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"by_support": True, "queue": d.label,
                                                     **({"engine_not_reached": unreached} if unreached else {})})
        rs = settings_store.load(db)
        outbox.queue(db, [Mail(to=job.user.email, subject=f"Stopped: {job.filename}", text=(
            f"Your document \"{job.filename}\" was stopped by support while it was being processed. "
            f"No pages were charged.\n\nYou can send it again at any time.\n\n{rs.service_name}\n"))],
            rs.mail_from_name, "job", about=f"job:{job.id}")
    elif d.kind == "edits":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None:
            raise not_found("Saved edit")
        if v.status not in ("submitted", "processing"):
            raise ApiError(409, "not_in_progress", "Only saves in progress can be stopped; this one isn't any more.")
        unreached = _stop_at_engine(v.engine_job_id)
        v.status, v.error_code, v.finished_at = "failed", "canceled", _now()
        v.error_message = "This save was stopped by support. The previous version is unchanged."
        if v.review_status in ("queued", "running"):
            v.review_status, v.review_error = "failed", "The version wasn't saved, so it wasn't reviewed."
        audit.record(db, request, "version.stopped", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number,
                                                       **({"engine_not_reached": unreached} if unreached else {})})
    elif d.kind == "reviews":
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.id == iid).with_for_update())
        if v is None or v.status != "ready":
            raise not_found("Review")
        if v.review_status != "running":
            raise ApiError(409, "not_in_progress", "Only reviews in progress can be stopped; this one isn't any more.")
        unreached = _stop_at_engine(v.review_engine_job_id)
        v.review_status, v.review_error = "failed", "This review was stopped by support."
        audit.record(db, request, "version.review_stopped", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number,
                                                       **({"engine_not_reached": unreached} if unreached else {})})
    else:
        raise ApiError(409, "not_stoppable", "Emails are sent at once; there's nothing in progress to stop.")


def remove(db: Session, request: Request | None, key: str, item_id: str, actor: User) -> None:
    """Take a failed item off its queue's list. Nothing about the item changes (the document stays in the person's
    list as failed, with its history); it's just no longer shown, or counted, as a failure to deal with."""
    d = _definition(db, key)
    iid = _uuid(item_id)
    if d.kind == "documents":
        job = db.get(Job, iid)
        if job is None or d.service not in job_services(job):
            raise not_found("Document")
        failed, target, group, what = job.status == "failed", job.id, job.group_id, {"file": job.filename}
    elif d.kind in ("edits", "reviews"):
        v = db.get(DocumentVersion, iid)
        if v is None:
            raise not_found("Saved edit" if d.kind == "edits" else "Review")
        failed = v.status == "failed" if d.kind == "edits" else v.review_status == "failed"
        target, group, what = v.job_id, v.job.group_id, {"version": v.number}
    else:
        m = db.get(MailOutbox, iid)
        if m is None:
            raise not_found("Email")
        failed, target, group, what = m.status == "failed", m.id, None, {"to": m.to_addr, "subject": m.subject}
    if not failed:
        raise ApiError(409, "not_failed", "Only failed items can be removed from the list. Cancel a waiting one, "
                                          "or stop one in progress.")
    row = db.get(QueueRemoval, (d.kind, iid))
    if row is None:
        db.add(QueueRemoval(kind=d.kind, item_id=iid, removed_by=actor.email, removed_at=_now()))
    else:  # removed before, and it has failed again since
        row.removed_by, row.removed_at = actor.email, _now()
    audit.record(db, request, "queue.item_removed", actor=actor,
                 target_type="mail" if d.kind == "mail" else "job", target_id=target, group_id=group,
                 details={"queue": d.label, **what})


def forget_removal(db: Session, kind: str, item_id: uuid.UUID) -> None:
    """An item being tried again is a new attempt: if it fails again, it's listed again."""
    row = db.get(QueueRemoval, (kind, item_id))
    if row is not None:
        db.delete(row)


def process_now(db: Session, request: Request | None, key: str, item_id: str, actor: User) -> None:
    """A waiting large document goes ahead of the large-document rules (it still needs a free slot, and waits
    for a paused service or a daily limit like any other)."""
    d = _definition(db, key)
    if d.kind != "documents":
        raise ApiError(409, "not_a_document", "Only documents can be processed now.")
    job = db.scalar(select(Job).where(Job.id == _uuid(item_id)).with_for_update())
    if job is None or d.service not in job_services(job):
        raise not_found("Document")
    if job.status != "queued":
        raise ApiError(409, "not_waiting", "This document isn't waiting any more.")
    if job.expedited_at is None:
        job.expedited_at, job.expedited_by = _now(), actor.email
        audit.record(db, request, "job.expedited", actor=actor, target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"pages": job.pages, "queue": d.label})
