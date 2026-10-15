"""Removing a document's files before the end of retention, and deleting documents.

People mark a finished document done; its files go once the hold (early_removal_hold_hours) ends, so a mistake can
be undone. Super admins can remove files at once, or delete a document: it leaves every document list but its record
stays, so reports, usage and the page ledger are unchanged. Removing files is the same as at the end of retention:
stored files and versions, and help requests about the document (unless saved).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import audit, helpdesk, settings_store
from portal.errors import ApiError
from portal.models import ACTIVE_JOB_STATUSES, HelpRequest, Job, User
from portal.storage import get_storage, job_prefix

OPEN_HELP = ("open", "in_progress", "waiting")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def remove_at(job: Job, hold_hours: int) -> datetime | None:
    """When a document marked done loses its files (None: not marked, or already gone)."""
    if job.removal_requested_at is None or job.files_deleted_at is not None:
        return None
    return job.removal_requested_at + timedelta(hours=hold_hours)


def remove_files(db: Session, jobs: list[Job], now: datetime | None = None) -> tuple[int, int]:
    """Delete the jobs' stored files and help requests about them; (stored objects, help requests) removed. The
    caller commits."""
    now = now or _now()
    storage = get_storage()
    objects = 0
    for job in jobs:
        objects += storage.delete_prefix(job_prefix(job.group_id, job.user_id, job.id))
        job.files_deleted_at = now
    help_requests, help_objects = helpdesk.expire_with_documents(db, [j.id for j in jobs])
    return objects + help_objects, help_requests


def refusal(db: Session, job: Job, *, for_person: bool) -> str | None:
    """Why this document's files can't be removed early (None: they can)."""
    if job.deleted_at is not None:
        return "deleted"
    if job.files_deleted_at is not None:
        return "files_gone"
    if job.status in ACTIVE_JOB_STATUSES:
        return "not_finished"
    if for_person and db.scalar(select(HelpRequest.id).where(HelpRequest.job_id == job.id,
                                                              HelpRequest.status.in_(OPEN_HELP)).limit(1)):
        return "help_open"
    return None


MESSAGES = {
    "deleted": "This document was deleted.",
    "files_gone": "Its files have already been removed.",
    "not_finished": "It's still waiting or being processed; cancel it first.",
    "help_open": "It has an open help request. Its files can be removed once that's finished.",
    "already": "It's already marked for removal.",
    "not_marked": "It isn't marked for removal.",
}


def request_removal(db: Session, request: Request | None, job: Job, user: User) -> None:
    """The owner is done with the document: its files go when the hold ends."""
    why = refusal(db, job, for_person=True) or ("already" if job.removal_requested_at is not None else None)
    if why:
        raise ApiError(409, why, MESSAGES[why])
    job.removal_requested_at = _now()
    job.removal_requested_by = user.email
    hold = settings_store.load(db).early_removal_hold_hours
    audit.record(db, request, "job.removal_requested", actor=user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"filename": job.filename, "hold_hours": hold})


def cancel_removal(db: Session, request: Request | None, job: Job, user: User) -> None:
    """Keep it: back to the normal retention period."""
    if job.removal_requested_at is None or job.files_deleted_at is not None:
        why = "files_gone" if job.files_deleted_at is not None else "not_marked"
        raise ApiError(409, why, MESSAGES[why])
    job.removal_requested_at = None
    job.removal_requested_by = None
    audit.record(db, request, "job.removal_canceled", actor=user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"filename": job.filename})


def admin_remove(db: Session, request: Request | None, jobs: list[Job], user: User, *, delete: bool,
                 reason: str) -> dict:
    """Super admins: remove files now, or delete the documents (files now, record hidden from document lists).
    Documents still waiting or in progress are skipped. Returns {"done": [...ids], "skipped": [{id, reason}]}."""
    now = _now()
    done: list[Job] = []
    skipped = []
    for job in jobs:
        if job.deleted_at is not None:
            skipped.append({"id": str(job.id), "reason": "deleted", "message": MESSAGES["deleted"]})
        elif job.status in ACTIVE_JOB_STATUSES:
            skipped.append({"id": str(job.id), "reason": "not_finished", "message": MESSAGES["not_finished"]})
        elif not delete and job.files_deleted_at is not None:
            skipped.append({"id": str(job.id), "reason": "files_gone", "message": MESSAGES["files_gone"]})
        else:
            done.append(job)
    remove_files(db, [j for j in done if j.files_deleted_at is None], now)
    for job in done:
        if delete:
            job.deleted_at = now
            job.deleted_by = user.email
        audit.record(db, request, "job.deleted" if delete else "job.files_removed", actor=user, target_type="job",
                     target_id=job.id, group_id=job.group_id,
                     details={"filename": job.filename, "owner": job.user.email, "reason": reason})
    return {"done": [str(j.id) for j in done], "skipped": skipped}


def due(db: Session, hold_hours: int, now: datetime, limit: int = 500) -> list[Job]:
    """Sweep: documents marked done whose hold has ended."""
    return list(db.scalars(select(Job).where(
        Job.removal_requested_at.is_not(None), Job.files_deleted_at.is_(None),
        Job.removal_requested_at <= now - timedelta(hours=hold_hours),
        Job.status.not_in(ACTIVE_JOB_STATUSES)).with_for_update(skip_locked=True).limit(limit)))


def parse_ids(ids: list[str]) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(i) for i in ids]
    except ValueError:
        raise ApiError(422, "bad_id", "One of the documents isn't valid.") from None
