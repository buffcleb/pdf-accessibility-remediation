"""Document versions for the tag manager: the original, the engine's outputs, and human-reviewed
versions saved from tag edits. Engine outputs are never changed; each save makes a new version."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal import approval, audit, queues, services, tagmarks
from portal.db import get_db
from portal.deps import Principal, current_principal
from portal.errors import ApiError, not_found
from portal.models import DocumentVersion, Job, JobFile, TagDraft
from portal.routers.jobs import _file, _record_access, _visible_job
from portal.security.ratelimit import RateLimiter, get_rate_limiter
from portal.storage import get_storage, safe_name

router = APIRouter(tags=["versions"])

OPS = {"set_role", "set_alt", "set_actual_text", "set_decorative", "set_lang", "set_cell", "move", "merge", "split",
       "assign", "tag_artifacts", "delete", "make_list", "indent", "outdent", "build_list", "take_pieces", "cut_area", "make_toc",
       "set_headers", "link_note"}
MAX_EDITS = 5000
MAX_EDITS_BYTES = 8 * 1024 * 1024  # all of one save's or draft's edits together, as JSON
MAX_PENDING_PER_JOB = 3
# After saving into the same reviewed version, how long the file before is kept so the save can be undone
UNDO_HOURS = 24


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
    # Saved into the same reviewed version: the number of the one it's replacing (while it's being saved), and
    # until when that save can be undone (once it has taken its place)
    replacing: int | None = None
    undo_until: datetime | None = None


class VersionsOut(BaseModel):
    can_edit: bool
    versions: list[VersionOut]


class VersionCreate(BaseModel):
    based_on: int = Field(ge=0)
    edits: list[dict] = Field(min_length=1, max_length=MAX_EDITS)
    note: str | None = Field(None, max_length=2000)
    # "sampled": also run the AI tag review (flagged items plus a sample) on the saved version
    review: Literal["none", "sampled", "full"] = "none"
    # Save into the reviewed version being edited (it keeps its name; the file before is kept a day for undo)
    # instead of making a new one
    replace: bool = False


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
        reviewed_at=v.reviewed_at, review_error=v.review_error,
        replacing=numbers.get(v.replaces_id) if v.replaces_id and v.status != "ready" else None,
        undo_until=_undo_until(db, v))


def _undo_until(db: Session, v: DocumentVersion) -> datetime | None:
    """Until when the save that made this version can be undone: while the version it replaced is still kept."""
    if v.replaces_id is None or v.status != "ready" or v.superseded_at is not None:
        return None
    before = db.get(DocumentVersion, v.replaces_id)
    if before is None or before.superseded_at is None:
        return None
    until = before.superseded_at + timedelta(hours=UNDO_HOURS)
    return until if until > datetime.now(timezone.utc) else None


def _versions(db: Session, job: Job, kept: bool = False) -> list[DocumentVersion]:
    """The document's versions in order. kept: also the ones a later save replaced (kept a day for undo)."""
    q = select(DocumentVersion).where(DocumentVersion.job_id == job.id)
    if not kept:
        q = q.where(DocumentVersion.superseded_at.is_(None))
    return list(db.scalars(q.order_by(DocumentVersion.number)))


def _version(db: Session, job: Job, number: int) -> DocumentVersion:
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == job.id, DocumentVersion.number == number,
                                                DocumentVersion.superseded_at.is_(None)))
    if v is None:
        raise not_found("Version")
    return v


_ORDINAL = re.compile(r"^Human reviewed (\d+)")


def _next_reviewed(vs: list[DocumentVersion]) -> int:
    """The number for a new "Human reviewed N": one more than the highest in use (a version saved into keeps its
    number, so counting rows would hand a number out twice)."""
    used = [int(m.group(1)) for v in vs if v.kind == "reviewed" and (m := _ORDINAL.match(v.label or ""))]
    return max(used, default=0) + 1


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
    data = b"".join(get_storage().get_stream(f.s3_key))
    # Where the document came tagged and its tags were kept, say which tags were its own
    job = db.get(Job, job_id)
    if job is not None and (job.result_summary or {}).get("tags_kept"):
        try:
            tagmap = json.loads(data)
            first = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == job_id,
                                                            DocumentVersion.kind == "original"))
            if first is not None and first.number == number:
                tagmarks.mark_all(tagmap)
            elif first is not None and first.tagmap_file_id is not None:
                original = db.get(JobFile, first.tagmap_file_id)
                if original is not None:
                    tagmarks.mark_original(tagmap, json.loads(b"".join(get_storage().get_stream(original.s3_key))))
            data = json.dumps(tagmap, separators=(",", ":")).encode()
        except (ValueError, KeyError, TypeError):
            pass  # an old or odd tag map: served as it is, unmarked
    return Response(data, media_type="application/json")


def _check_edits(edits: list[dict]) -> None:
    """Shape checks; the engine validates each edit against the document itself."""
    total = 0
    for n, op in enumerate(edits, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS or not isinstance(op.get("element"), int):
            raise ApiError(422, "invalid_edit", f"Edit {n} isn't a valid tag edit.")
        # An area's list of pieces can be long (a whole page); everything else is small
        size = len(json.dumps(op))
        if size > (64000 if op["op"] in ("assign", "tag_artifacts", "build_list", "take_pieces") else 8000):
            raise ApiError(422, "invalid_edit", f"Edit {n} is too large.")
        # Together, too: the per-edit caps alone allow hundreds of megabytes a draft, kept in the database
        total += size
        if total > MAX_EDITS_BYTES:
            raise ApiError(413, "edits_too_large", f"These edits add up to more than {MAX_EDITS_BYTES // 1024 // 1024} MB; "
                           "save some of them first.")


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
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
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
    if body.replace and base.kind != "reviewed":
        raise ApiError(422, "not_replaceable", "Only a human-reviewed version can be saved into. Save this as a new "
                                               "reviewed version.")
    vs = _versions(db, job, kept=True)
    v = DocumentVersion(job_id=job.id, number=max(x.number for x in vs) + 1, kind="reviewed",
                        label=base.label if body.replace else f"Human reviewed {_next_reviewed(vs)}",
                        replaces_id=base.id if body.replace else None,
                        based_on_id=base.id, created_by=p.user.id,
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
                                                 "ai_review": body.review, "saved_into": base.label if body.replace else None,
                                                 "owner": job.user.email, "by_admin": job.user_id != p.user.id})
    # What was a draft is now this version
    draft = db.get(TagDraft, (job.id, p.user.id))
    if draft is not None:
        db.delete(draft)
    db.commit()
    return _version_out(db, v, {x.id: x.number for x in vs} | {v.id: v.number})


@router.post("/jobs/{job_id}/versions/{number}/undo-save", response_model=VersionOut)
def undo_save(job_id: uuid.UUID, number: int, request: Request, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    """Undo a save into a reviewed version, within a day: the version as it was before comes back, and the one the
    save made is removed with its files."""
    approval.require_approved(p.user)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise not_found("Job")
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        raise ApiError(403, "forbidden", "Only the document's owner or their group admins can edit its tags.")
    v = _version(db, job, number)
    if _undo_until(db, v) is None:
        raise ApiError(409, "nothing_to_undo", "There's no earlier file kept for this version (it's kept for "
                                               f"{UNDO_HOURS} hours after a save into it).")
    if db.scalar(select(func.count()).select_from(DocumentVersion).where(
            DocumentVersion.job_id == job.id, DocumentVersion.based_on_id == v.id,
            DocumentVersion.status.in_(("queued", "submitted", "processing")))):
        raise ApiError(409, "save_in_progress", "A save based on this version is still being processed. Wait for it "
                                                "to finish.")
    before = db.get(DocumentVersion, v.replaces_id)
    before.superseded_at = None
    get_storage().delete_prefix(version_prefix(job, v.number))
    audit.record(db, request, "version.save_undone", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": before.number, "label": before.label, "removed": v.number})
    db.delete(v)
    db.flush()
    vs = _versions(db, job, kept=True)
    db.commit()
    return _version_out(db, before, {x.id: x.number for x in vs})


def version_prefix(job: Job, number: int) -> str:
    from portal.storage import job_prefix

    return job_prefix(job.group_id, job.user_id, job.id) + f"versions/{number}/"


# --- unsaved edits, kept between sessions --------------------------------------------------------------------------

class DraftOut(BaseModel):
    user_id: uuid.UUID
    user_email: str
    based_on: int
    edits: list[dict]
    updated_at: datetime


class DraftInfo(BaseModel):
    """Someone else's draft: who, when, how much. Its edits are fetched only if the person asks to start from them."""
    user_id: uuid.UUID
    user_email: str
    based_on: int
    edits_count: int
    updated_at: datetime


class DraftsOut(BaseModel):
    mine: DraftOut | None
    others: list[DraftInfo]


class DraftSave(BaseModel):
    based_on: int = Field(ge=0)
    edits: list[dict] = Field(max_length=MAX_EDITS)


def _draft_out(d: TagDraft) -> DraftOut:
    return DraftOut(user_id=d.user_id, user_email=d.user_email, based_on=d.based_on, edits=d.edits, updated_at=d.updated_at)


def _editable_job(db: Session, job_id: uuid.UUID, p: Principal) -> Job:
    approval.require_approved(p.user)
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        raise ApiError(403, "forbidden", "Only the document's owner or their group admins can edit its tags.")
    return job


@router.get("/jobs/{job_id}/tag-draft", response_model=DraftsOut)
def get_drafts(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The caller's unsaved edits on this document, and who else has some (for people who can edit it)."""
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        return DraftsOut(mine=None, others=[])
    drafts = list(db.scalars(select(TagDraft).where(TagDraft.job_id == job.id).order_by(TagDraft.updated_at.desc())))
    mine = next((d for d in drafts if d.user_id == p.user.id), None)
    return DraftsOut(mine=_draft_out(mine) if mine else None,
                     others=[DraftInfo(user_id=d.user_id, user_email=d.user_email, based_on=d.based_on,
                                       edits_count=len(d.edits), updated_at=d.updated_at)
                             for d in drafts if d.user_id != p.user.id])


@router.put("/jobs/{job_id}/tag-draft", response_model=DraftOut | None)
def save_draft(job_id: uuid.UUID, body: DraftSave, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    """Keep the caller's unsaved edits (replacing what was kept). No edits: the draft is removed."""
    job = _editable_job(db, job_id, p)
    draft = db.get(TagDraft, (job.id, p.user.id))
    if not body.edits:
        if draft is not None:
            db.delete(draft)
            db.commit()
        return None
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
    _version(db, job, body.based_on)
    _check_edits(body.edits)
    now = datetime.now(timezone.utc)
    if draft is None:
        draft = TagDraft(job_id=job.id, user_id=p.user.id, user_email=p.user.email, based_on=body.based_on,
                         edits=body.edits, created_at=now, updated_at=now)
        db.add(draft)
    else:
        draft.based_on, draft.edits, draft.updated_at, draft.user_email = body.based_on, body.edits, now, p.user.email
    db.commit()
    return _draft_out(draft)


@router.delete("/jobs/{job_id}/tag-draft", status_code=204)
def discard_draft(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    job = _visible_job(db, job_id, p)
    draft = db.get(TagDraft, (job.id, p.user.id))
    if draft is not None:
        db.delete(draft)
        db.commit()
    return Response(status_code=204)


@router.get("/jobs/{job_id}/tag-draft/{user_id}", response_model=DraftOut)
def someone_elses_draft(job_id: uuid.UUID, user_id: uuid.UUID, request: Request,
                        p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Another person's unsaved edits on this document, to start your own from. Theirs stay as they are."""
    job = _editable_job(db, job_id, p)
    draft = db.get(TagDraft, (job.id, user_id))
    if draft is None:
        raise not_found("Draft")
    audit.record(db, request, "draft.copied", actor=p.user, target_type="job", target_id=job.id, group_id=job.group_id,
                 details={"from": draft.user_email, "edits": len(draft.edits)})
    db.commit()
    return _draft_out(draft)


class DescribeRequest(BaseModel):
    page: int = Field(..., ge=1)
    bbox: list[float] = Field(..., min_length=4, max_length=4)  # [x0, y0, x1, y1], the tag map's page units
    long: bool = False  # a scheme: room for its steps


DESCRIBE_PER_HOUR = 120  # per person: enough to describe every part of a long document, not a runaway loop


@router.post("/jobs/{job_id}/versions/{number}/describe")
def describe_area(job_id: uuid.UUID, number: int, body: DescribeRequest, request: Request,
                  p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                  limiter: RateLimiter = Depends(get_rate_limiter)):
    """Alt text for an area of a page, written by the AI at the document's AI level: one AI call, recorded on the
    document's cost. For people "Describe with AI" is allowed for (the service default, their group or them)."""
    from portal import ai_describe
    from portal.engine import EngineError, EngineUnavailable, get_engine_client
    from portal.worker import _ai_choice
    approval.require_approved(p.user)
    job = _visible_job(db, job_id, p)
    if not can_edit(p, job):
        raise ApiError(403, "forbidden", "Only the document's owner or their group admins can do this.")
    if not ai_describe.allowed(db, p.user):
        raise ApiError(403, "ai_describe_off", "Describing with AI isn't turned on for you. A group admin can turn it on.")
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
    if body.bbox[2] - body.bbox[0] < 1 or body.bbox[3] - body.bbox[1] < 1:
        raise ApiError(422, "invalid", "Draw an area with some width and height.")
    v = _version(db, job, number)
    if v.status != "ready" or v.pdf_file_id is None:
        raise ApiError(422, "not_ready", "That version isn't ready yet.")
    ai = _ai_choice(db, job)
    if ai is None:
        raise ApiError(409, "no_ai_level", "This document wasn't processed with an AI level, so there's none to describe with.")
    if not limiter.hit("describe", str(p.user.id), DESCRIBE_PER_HOUR, 3600):
        raise ApiError(429, "rate_limited", "That's a lot of descriptions in an hour. Try again a little later.")
    f = db.get(JobFile, v.pdf_file_id)
    import tempfile
    with tempfile.TemporaryFile() as tmp:
        get_storage().get_to_file(f.s3_key, tmp)
        tmp.seek(0)
        try:
            out = get_engine_client().describe(f.name, tmp, body.page, body.bbox, ai.model, long=body.long)
        except EngineUnavailable:
            raise ApiError(503, "ai_unavailable", "The AI couldn't be reached just now. Try again in a minute.")
        except EngineError as e:
            raise ApiError(422, "describe_failed", f"That area couldn't be described: {e}")
    cost = float(out.get("cost_usd") or 0)
    prev = job.cost or {}
    job.cost = {**prev, "describe_usd": round(float(prev.get("describe_usd") or 0) + cost, 6),
                "describe_calls": int(prev.get("describe_calls") or 0) + 1}
    audit.record(db, request, "version.ai_described", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number, "page": body.page, "cost_usd": round(cost, 6),
                                                 "model": out.get("model")})
    db.commit()
    return {"alt": out.get("alt") or "", "kind": out.get("kind"), "decorative": bool(out.get("decorative")),
            "cost_usd": round(cost, 6)}


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
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
    v = _version(db, job, number)
    if v.kind not in ("reviewed", "corrected"):
        raise ApiError(422, "not_reviewable", "Only human-reviewed and corrected versions can be re-reviewed here.")
    if v.status != "ready" or v.pdf_file_id is None:
        raise ApiError(422, "not_reviewable", "That version isn't ready yet.")
    if v.review_status in ("queued", "running"):
        raise ApiError(409, "review_running", "A review of this version is already running.")
    if full and not services.full_review_allowed(db, job.ai_tier):
        raise ApiError(422, "full_review_not_allowed", "Reviewing every figure isn't available with this document's AI level.")
    queues.forget_removal(db, "reviews", v.id)  # asked for again: a new failure would be listed again
    v.review, v.review_status, v.review_error = ("full" if full else "sampled"), "queued", None
    v.review_engine_job_id, v.review_requested_by = None, p.user.email
    audit.record(db, request, "version.review_requested", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number, "kind": v.review,
                                                 "by_admin": job.user_id != p.user.id})
    db.commit()
    vs = _versions(db, job)
    return _version_out(db, v, {x.id: x.number for x in vs})
