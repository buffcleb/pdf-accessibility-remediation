"""Everything that waits its turn, in one place for super admins: documents (one queue per processing service
and AI level), saved edits, AI reviews of saved versions, and outgoing email.

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
from portal.models import DocumentVersion, Job, MailOutbox, QueuePause, Service, User

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


def _counts(db: Session, d: QueueDef) -> dict:
    since = _now() - RECENT

    def n(stmt) -> int:
        return db.scalar(stmt) or 0

    if d.kind == "documents":
        base = select(func.count()).select_from(Job).where(_job_filter(d))
        oldest = select(func.min(Job.created_at)).where(_job_filter(d), Job.status == "queued")
        return {"waiting": n(base.where(Job.status == "queued")),
                "active": n(base.where(Job.status.in_(("submitted", "processing")))),
                "failed": n(base.where(Job.status == "failed", Job.finished_at > since)),
                "oldest_waiting": db.scalar(oldest)}
    if d.kind == "edits":
        base = select(func.count()).select_from(DocumentVersion)
        return {"waiting": n(base.where(DocumentVersion.status == "queued")),
                "active": n(base.where(DocumentVersion.status.in_(("submitted", "processing")))),
                "failed": n(base.where(DocumentVersion.status == "failed", DocumentVersion.finished_at > since)),
                "oldest_waiting": db.scalar(select(func.min(DocumentVersion.created_at))
                                            .where(DocumentVersion.status == "queued"))}
    if d.kind == "reviews":
        base = select(func.count()).select_from(DocumentVersion).where(DocumentVersion.status == "ready")
        return {"waiting": n(base.where(DocumentVersion.review_status == "queued")),
                "active": n(base.where(DocumentVersion.review_status == "running")),
                "failed": n(base.where(DocumentVersion.review_status == "failed",
                                       DocumentVersion.created_at > since)),
                "oldest_waiting": db.scalar(select(func.min(DocumentVersion.created_at)).where(
                    DocumentVersion.status == "ready", DocumentVersion.review_status == "queued"))}
    base = select(func.count()).select_from(MailOutbox)
    return {"waiting": n(base.where(MailOutbox.status == "queued")), "active": 0,
            "failed": n(base.where(MailOutbox.status == "failed", MailOutbox.created_at > since)),
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
                 "failed": (Job.status == "failed") & (Job.finished_at > since)}[state]
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
                "can_cancel": state == "waiting",
                "can_process_now": state == "waiting" and 0 < threshold < j.pages and j.expedited_at is None,
            })
    elif d.kind in ("edits", "reviews"):
        if d.kind == "edits":
            where = {"waiting": DocumentVersion.status == "queued",
                     "active": DocumentVersion.status.in_(("submitted", "processing")),
                     "failed": (DocumentVersion.status == "failed") & (DocumentVersion.finished_at > since)}[state]
        else:
            where = (DocumentVersion.status == "ready") & {
                "waiting": DocumentVersion.review_status == "queued",
                "active": DocumentVersion.review_status == "running",
                "failed": (DocumentVersion.review_status == "failed") & (DocumentVersion.created_at > since)}[state]
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
                "can_cancel": state == "waiting",
            })
    else:
        if state == "active":
            return {"queue": d.label, "state": state, "items": []}
        where = MailOutbox.status == "queued" if state == "waiting" else (
            (MailOutbox.status == "failed") & (MailOutbox.created_at > since))
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
            })
    return {"queue": d.label, "state": state, "items": rows}


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
        v.review_status, v.review_error, v.review_engine_job_id, v.review_submitted_at = "queued", None, None, None
        audit.record(db, request, "version.review_retried", actor=actor, target_type="job", target_id=v.job_id,
                     group_id=v.job.group_id, details={"version": v.number})
    else:
        m = db.scalar(select(MailOutbox).where(MailOutbox.id == iid).with_for_update())
        if m is None:
            raise not_found("Email")
        if m.status != "failed":
            raise ApiError(409, "not_failed", "Only failed emails can be tried again.")
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
