"""Document versions for the tag viewer: the original, the engine's outputs, and human-reviewed
versions saved from tag edits. Engine outputs are never changed; each save makes a new version."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal import approval, audit, services
from portal.db import get_db
from portal.deps import Principal, current_principal
from portal.errors import ApiError, not_found
from portal.models import DocumentVersion, Job, JobFile
from portal.routers.jobs import _file, _record_access, _visible_job
from portal.storage import get_storage, safe_name

router = APIRouter(tags=["versions"])

OPS = {"set_role", "set_alt", "set_actual_text", "set_decorative", "set_lang", "set_cell", "move", "merge", "split"}
MAX_EDITS = 5000
MAX_PENDING_PER_JOB = 3


class VersionOut(BaseModel):
    id: uuid.UUID
    number: int
    kind: str
    label: str
    status: str
    based_on: int | None
    created_by_email: str | None
    note: str | None
    edits_count: int
    summary: dict | None
    error_message: str | None
    has_tagmap: bool
    pdf_file_id: uuid.UUID | None
    pdf_name: str | None
    pdf_size: int | None
    verapdf_file_id: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    review: str | None
    review_status: str | None
    reviewed_at: datetime | None
    review_error: str | None


class VersionsOut(BaseModel):
    can_edit: bool
    versions: list[VersionOut]


class VersionCreate(BaseModel):
    based_on: int = Field(ge=0)
    edits: list[dict] = Field(min_length=1, max_length=MAX_EDITS)
    note: str | None = Field(None, max_length=2000)
    # "sampled": also run the AI tag review (flagged items plus a sample) on the saved version
    review: Literal["none", "sampled", "full"] = "none"


def can_edit(p: Principal, job: Job) -> bool:
    """The owner (once approved) and admins of the job's group. Super admins alone can view but not edit."""
    return ((job.user_id == p.user.id and p.user.approved)
            or (job.group_id is not None and job.group_id in p.user.admin_group_ids()))


def _version_out(db: Session, v: DocumentVersion, numbers: dict[uuid.UUID, int]) -> VersionOut:
    pdf = db.get(JobFile, v.pdf_file_id) if v.pdf_file_id else None
    return VersionOut(
        id=v.id, number=v.number, kind=v.kind, label=v.label, status=v.status,
        based_on=numbers.get(v.based_on_id) if v.based_on_id else None, created_by_email=v.created_by_email,
        note=v.note, edits_count=len(v.edits or []), summary=v.summary, error_message=v.error_message,
        has_tagmap=v.tagmap_file_id is not None, pdf_file_id=v.pdf_file_id, pdf_name=pdf.name if pdf else None,
        pdf_size=pdf.size_bytes if pdf else None, verapdf_file_id=v.verapdf_file_id,
        created_at=v.created_at, finished_at=v.finished_at, review=v.review, review_status=v.review_status,
        reviewed_at=v.reviewed_at, review_error=v.review_error)


def _versions(db: Session, job: Job) -> list[DocumentVersion]:
    return list(db.scalars(select(DocumentVersion).where(DocumentVersion.job_id == job.id)
                           .order_by(DocumentVersion.number)))


def _version(db: Session, job: Job, number: int) -> DocumentVersion:
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == job.id, DocumentVersion.number == number))
    if v is None:
        raise not_found("Version")
    return v


@router.get("/jobs/{job_id}/versions", response_model=VersionsOut)
def list_versions(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    job = _visible_job(db, job_id, p)
    vs = _versions(db, job)
    numbers = {v.id: v.number for v in vs}
    return VersionsOut(can_edit=can_edit(p, job), versions=[_version_out(db, v, numbers) for v in vs])


def _version_file(db: Session, request: Request, p: Principal, job_id: uuid.UUID, number: int, which: str,
                  reason: str | None = None):
    job = _visible_job(db, job_id, p)
    v = _version(db, job, number)
    file_id = {"pdf": v.pdf_file_id, "tagmap": v.tagmap_file_id}[which]
    if file_id is None:
        raise not_found("File")
    f = _file(db, job, file_id)
    _record_access(db, request, p, job, f, reason)
    return f


@router.get("/jobs/{job_id}/versions/{number}/pdf")
def version_pdf(job_id: uuid.UUID, number: int, request: Request, reason: str | None = Query(None, max_length=500),
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = _version_file(db, request, p, job_id, number, "pdf", reason)
    return StreamingResponse(get_storage().get_stream(f.s3_key), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{safe_name(f.name)}"',
                                      "Content-Length": str(f.size_bytes)})


@router.get("/jobs/{job_id}/versions/{number}/tagmap")
def version_tagmap(job_id: uuid.UUID, number: int, request: Request, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    f = _version_file(db, request, p, job_id, number, "tagmap")
    return Response(b"".join(get_storage().get_stream(f.s3_key)), media_type="application/json")


def _check_edits(edits: list[dict]) -> None:
    """Shape checks; the engine validates each edit against the document itself."""
    for n, op in enumerate(edits, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS or not isinstance(op.get("element"), int):
            raise ApiError(422, "invalid_edit", f"Edit {n} isn't a valid tag edit.")
        if len(json.dumps(op)) > 8000:
            raise ApiError(422, "invalid_edit", f"Edit {n} is too large.")


@router.post("/jobs/{job_id}/versions", response_model=VersionOut, status_code=201)
def create_version(job_id: uuid.UUID, body: VersionCreate, request: Request,
                   p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    approval.require_approved(p.user)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())  # serializes version numbering
    if job is None:
        raise not_found("Job")
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        raise ApiError(403, "forbidden", "Only the document's owner or their group admins can edit its tags.")
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been deleted under the retention policy.")
    base = _version(db, job, body.based_on)
    if base.kind == "original":
        raise ApiError(422, "not_editable", "Edit one of the remediated versions, not the original.")
    if base.status != "ready" or base.pdf_file_id is None or base.tagmap_file_id is None:
        raise ApiError(422, "not_editable", "That version isn't ready to edit.")
    _check_edits(body.edits)
    if body.review == "full" and not services.full_review_allowed(db, job.ai_tier):
        raise ApiError(422, "full_review_not_allowed", "Reviewing every figure isn't available with this document's AI level.")
    pending = db.scalar(select(func.count()).select_from(DocumentVersion).where(
        DocumentVersion.job_id == job.id, DocumentVersion.status.in_(("queued", "submitted", "processing"))))
    if pending >= MAX_PENDING_PER_JOB:
        raise ApiError(429, "too_many_saves", "Earlier saves for this document are still being processed. "
                                              "Wait for them to finish.")
    vs = _versions(db, job)
    reviewed = sum(1 for v in vs if v.kind == "reviewed")
    v = DocumentVersion(job_id=job.id, number=max(x.number for x in vs) + 1, kind="reviewed",
                        label=f"Human reviewed {reviewed + 1}", based_on_id=base.id, created_by=p.user.id,
                        created_by_email=p.user.email, status="queued", edits=body.edits,
                        note=(body.note or "").strip() or None,
                        review=body.review if body.review != "none" else None,
                        review_status="queued" if body.review != "none" else None,
                        review_requested_by=p.user.email if body.review != "none" else None)
    db.add(v)
    db.flush()
    counts: dict[str, int] = {}
    for op in body.edits:
        counts[op["op"]] = counts.get(op["op"], 0) + 1
    audit.record(db, request, "version.created", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number, "based_on": base.number, "edits": counts,
                                                 "ai_review": body.review,
                                                 "owner": job.user.email, "by_admin": job.user_id != p.user.id})
    db.commit()
    return _version_out(db, v, {x.id: x.number for x in vs} | {v.id: v.number})


class ReviewRequest(BaseModel):
    full: bool = False  # review every figure instead of the flagged ones plus a sample


@router.post("/jobs/{job_id}/versions/{number}/review", response_model=VersionOut, status_code=202)
def review_version(job_id: uuid.UUID, number: int, request: Request, body: ReviewRequest | None = None,
                   p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Run the AI tag review (flagged items plus a sample, or every figure) on a saved reviewed version. The file
    isn't changed; the version's review findings are replaced when the review finishes."""
    approval.require_approved(p.user)
    full = bool(body and body.full)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise not_found("Job")
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        raise ApiError(403, "forbidden", "Only the document's owner or their group admins can request a review.")
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been deleted under the retention policy.")
    v = _version(db, job, number)
    if v.kind not in ("reviewed", "corrected"):
        raise ApiError(422, "not_reviewable", "Only human-reviewed and corrected versions can be re-reviewed here.")
    if v.status != "ready" or v.pdf_file_id is None:
        raise ApiError(422, "not_reviewable", "That version isn't ready yet.")
    if v.review_status in ("queued", "running"):
        raise ApiError(409, "review_running", "A review of this version is already running.")
    if full and not services.full_review_allowed(db, job.ai_tier):
        raise ApiError(422, "full_review_not_allowed", "Reviewing every figure isn't available with this document's AI level.")
    v.review, v.review_status, v.review_error = ("full" if full else "sampled"), "queued", None
    v.review_engine_job_id, v.review_requested_by = None, p.user.email
    audit.record(db, request, "version.review_requested", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number, "kind": v.review,
                                                 "by_admin": job.user_id != p.user.id})
    db.commit()
    vs = _versions(db, job)
    return _version_out(db, v, {x.id: x.number for x in vs})
