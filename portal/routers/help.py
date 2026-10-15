"""Help requests: asking for help with a document, the conversation about it, and the super admins' queue."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import approval, audit, helpdesk, settings_store
from portal.db import get_db
from portal.deps import Principal, current_principal, group_for_admin, require_super_admin
from portal.errors import ApiError, conflict, not_found, too_many
from portal.models import HELP_KINDS, HELP_STATUSES, DocumentVersion, Group, HelpAttachment, HelpRequest, Job, User
from portal.security.ratelimit import RateLimiter, get_rate_limiter
from portal.storage import get_storage, safe_name

router = APIRouter(tags=["help requests"])

NEW_PER_DAY = 5
MESSAGES_PER_HOUR = 30


class AttachmentOut(BaseModel):
    id: uuid.UUID
    name: str
    content_type: str
    size_bytes: int


class MessageOut(BaseModel):
    id: uuid.UUID
    kind: str  # message | note (super admins only) | event
    author_email: str
    author_role: str
    body: str
    created_at: datetime
    attachments: list[AttachmentOut]


class HelpSummary(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None
    filename: str
    user_email: str
    group_id: uuid.UUID | None
    group_name: str | None
    kind: str
    status: str
    version_number: int | None
    version_label: str | None
    page: int | None
    processing: str | None
    ai_tier: str | None
    assignee_email: str | None
    saved: bool
    needs_staff: bool
    unread: bool  # for the person who asked: something new since they last looked
    created_at: datetime
    updated_at: datetime
    waiting_since: datetime  # when the side that has to act last heard from the other


class HelpOut(HelpSummary):
    tag: dict | None
    role: str  # how the caller takes part: person | group_admin | super_admin
    messages: list[MessageOut]
    resolution: str | None  # what was done, as the person was told
    resolved_version_number: int | None
    resolved_version_label: str | None
    resolved_version_status: str | None  # a corrected file is checked before it's ready
    pages_refunded: int | None
    finished_at: datetime | None
    reopen_until: datetime | None  # the person can reopen it themselves until then
    can_reopen: bool
    pages_charged: int | None  # what the document cost, as a hint for refunds (super admins)


def _summary(req: HelpRequest, job: Job | None = None) -> dict:
    waiting = max((t for t in (req.person_wrote_at, req.created_at) if t), default=req.created_at) \
        if req.needs_staff else (req.status_changed_at or req.updated_at)
    return dict(
        id=req.id, job_id=req.job_id, filename=req.filename, user_email=req.user.email, group_id=req.group_id,
        group_name=req.group.name if req.group else None, kind=req.kind, status=req.status,
        version_number=req.version_number, version_label=req.version_label, page=req.page,
        processing=job.processing if job else None, ai_tier=job.ai_tier if job else None,
        assignee_email=req.assignee_email, saved=req.saved, needs_staff=req.needs_staff,
        unread=req.unread_for_person, created_at=req.created_at, updated_at=req.updated_at, waiting_since=waiting)


def _out(db: Session, req: HelpRequest, role: str) -> HelpOut:
    job = db.get(Job, req.job_id) if req.job_id else None
    msgs = [MessageOut(id=m.id, kind=m.kind, author_email=m.author_email, author_role=m.author_role,
                       body=helpdesk.event_text(m, role == "person") if m.kind == "event" else m.body,
                       created_at=m.created_at,
                       attachments=[AttachmentOut(id=a.id, name=a.name, content_type=a.content_type,
                                                  size_bytes=a.size_bytes) for a in m.attachments])
            for m in helpdesk.visible_messages(req, role)
            if m.kind != "event" or helpdesk.event_text(m, role == "person")]
    rv = None
    if req.job_id and req.resolved_version_number is not None:
        rv = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == req.job_id,
                                                     DocumentVersion.number == req.resolved_version_number))
    until = helpdesk.reopen_until(req, settings_store.load(db).help_reopen_days)
    can_reopen = not req.active and req.job_id is not None and (
        role == "super_admin" or (role == "person" and until is not None and helpdesk._now() <= until))
    return HelpOut(**_summary(req, job), tag=req.tag, role=role, messages=msgs, resolution=req.resolution,
                   resolved_version_number=req.resolved_version_number, resolved_version_label=rv.label if rv else None,
                   resolved_version_status=rv.status if rv else None, pages_refunded=req.pages_refunded,
                   finished_at=req.finished_at, reopen_until=until, can_reopen=can_reopen,
                   pages_charged=job.pages_charged if job and role == "super_admin" else None)


def _visible(db: Session, request_id: uuid.UUID, p: Principal, lock: bool = False) -> tuple[HelpRequest, str]:
    stmt = select(HelpRequest).where(HelpRequest.id == request_id)
    req = db.scalar(stmt.with_for_update() if lock else stmt)
    role = helpdesk.role_of(p.user, req) if req else None
    if req is None or role is None:
        raise not_found("Help request")
    return req, role


def _seen(db: Session, req: HelpRequest, role: str) -> None:
    if role == "person":
        req.person_seen_at = helpdesk._now()
        db.commit()


def _limits(db: Session) -> tuple[int, int]:
    rs = settings_store.load(db)
    return rs.help_max_file_mb, rs.help_max_request_mb


# --- the person ------------------------------------------------------------------------------------

@router.post("/jobs/{job_id}/help", response_model=HelpOut, status_code=201)
def ask_for_help(job_id: uuid.UUID, request: Request,
                 body: str = Form(..., min_length=1, max_length=helpdesk.MAX_BODY),
                 kind: str = Form("other"), version: int | None = Form(None, ge=0), page: int | None = Form(None, ge=1),
                 tag: str | None = Form(None, max_length=2000), files: list[UploadFile] = File(default=[]),
                 p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                 limiter: RateLimiter = Depends(get_rate_limiter)):
    """Ask for help with one of your own documents. One request at a time per document."""
    approval.require_approved(p.user)
    job = db.get(Job, job_id)
    if job is None or job.user_id != p.user.id:
        raise not_found("Job")
    failed = job.status == "failed"
    if not failed and (job.status != "completed" or job.files_deleted_at is not None):
        raise ApiError(422, "not_ready", "You can ask for help once the document has finished and while its files are kept.")
    if failed:
        kind, version, page, tag = "failed", None, None, None  # there is no result to point into
    elif kind not in HELP_KINDS or kind == "failed":
        raise ApiError(422, "invalid", "Choose a kind of problem from the list.")
    body = body.strip()
    if not body:
        raise ApiError(422, "invalid", "Say what's wrong.")
    existing = db.scalar(select(HelpRequest).where(HelpRequest.job_id == job.id,
                                                   HelpRequest.status.in_(("open", "in_progress", "waiting"))))
    if existing is not None:
        raise conflict("help_open", "There's already a help request for this document; add to it instead.")
    if not limiter.hit("help-new", str(p.user.id), NEW_PER_DAY, 86400):
        raise too_many()
    label = None
    if version is not None:
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == job.id, DocumentVersion.number == version))
        if v is None:
            raise ApiError(422, "invalid", "That version doesn't exist for this document.")
        label = v.label
    tag_data = None
    if tag:
        try:
            raw = json.loads(tag)
            tag_data = {"i": int(raw["i"]), "role": str(raw.get("role", ""))[:40], "text": str(raw.get("text", ""))[:200]}
        except (ValueError, KeyError, TypeError):
            raise ApiError(422, "invalid", "The tag reference isn't valid.") from None
    max_file, max_req = _limits(db)
    uploads = helpdesk.read_attachments(files, 0, max_file, max_req)

    req = HelpRequest(id=uuid.uuid4(), job_id=job.id, filename=job.filename, user_id=p.user.id, group_id=job.group_id,
                      kind=kind, status="open", version_number=version, version_label=label, page=page, tag=tag_data,
                      person_seen_at=helpdesk._now())
    db.add(req)
    try:
        db.flush()
    except IntegrityError:  # someone else just opened one for the same document
        db.rollback()
        raise conflict("help_open", "There's already a help request for this document; add to it instead.") from None
    first = helpdesk.add_message(db, req, p.user, "person", "message", body)
    helpdesk.store_attachments(db, req, first, uploads)
    audit.record(db, request, "help.requested", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"job_id": str(job.id), "kind": kind, "version": version,
                                                 "attachments": len(uploads)})
    db.flush()
    db.refresh(req)
    helpdesk.queue_mail(db, helpdesk.new_request_mails(db, req, first), req)
    req.staff_notified_at = helpdesk._now()
    db.commit()
    return _out(db, req, "person")


@router.get("/jobs/{job_id}/help", response_model=HelpOut | None)
def help_for_job(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The document's current help request (the active one, or the latest), if the caller may see it."""
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job")
    reqs = db.scalars(select(HelpRequest).where(HelpRequest.job_id == job.id)
                      .order_by(HelpRequest.created_at.desc())).all()
    req = next((r for r in reqs if r.active), reqs[0] if reqs else None)
    role = helpdesk.role_of(p.user, req) if req else None
    if req is None or role is None:
        if job.user_id != p.user.id and not p.is_super_admin and not (job.group_id and p.can_admin_group(job.group_id)):
            raise not_found("Job")
        return None
    _seen(db, req, role)
    return _out(db, req, role)


@router.get("/help/mine", response_model=list[HelpSummary])
def my_requests(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    reqs = db.scalars(select(HelpRequest).where(HelpRequest.user_id == p.user.id)
                      .order_by(HelpRequest.updated_at.desc()).limit(200))
    return [HelpSummary(**_summary(r)) for r in reqs]


# --- the conversation ------------------------------------------------------------------------------

@router.get("/help/{request_id}", response_model=HelpOut)
def get_request(request_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req, role = _visible(db, request_id, p)
    _seen(db, req, role)
    return _out(db, req, role)


@router.post("/help/{request_id}/messages", response_model=HelpOut, status_code=201)
def post_message(request_id: uuid.UUID, request: Request,
                 body: str = Form("", max_length=helpdesk.MAX_BODY), internal: bool = Form(False),
                 files: list[UploadFile] = File(default=[]), p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db), limiter: RateLimiter = Depends(get_rate_limiter)):
    """Write on a request, with files if needed. Internal notes are for super admins and aren't sent to anyone."""
    req, role = _visible(db, request_id, p, lock=True)
    body = body.strip()
    if internal and role != "super_admin":
        raise ApiError(403, "forbidden", "Only super admins can write internal notes.")
    if not req.active and role != "super_admin":
        raise ApiError(409, "help_finished",
                       "This request is finished. If there's still a problem, ask for help with the document again.")
    if not body and not any(f.filename for f in files):
        raise ApiError(422, "invalid", "Write a message or attach a file.")
    if not limiter.hit("help-msg", str(p.user.id), MESSAGES_PER_HOUR, 3600):
        raise too_many()
    max_file, max_req = _limits(db)
    uploads = helpdesk.read_attachments(files, helpdesk.attachments_bytes(db, req), max_file, max_req)
    msg = helpdesk.add_message(db, req, p.user, role, "note" if internal else "message", body)
    helpdesk.store_attachments(db, req, msg, uploads)
    if not internal:
        if role != "super_admin" and req.status == "waiting":
            helpdesk.set_status(db, req, "in_progress", None, "system")
        if role == "person":
            req.person_seen_at = helpdesk._now()  # their own reply isn't news to them
        if role == "super_admin":
            helpdesk.notify(db, req, "person")
        else:
            helpdesk.notify(db, req, "staff")
            if role == "group_admin":
                helpdesk.notify(db, req, "person")
    audit.record(db, request, "help.message", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"kind": msg.kind, "role": role, "attachments": len(uploads)})
    db.commit()
    db.refresh(req)
    return _out(db, req, role)


@router.get("/help/{request_id}/attachments/{attachment_id}")
def download_attachment(request_id: uuid.UUID, attachment_id: uuid.UUID, request: Request,
                        p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req, role = _visible(db, request_id, p)
    att = db.get(HelpAttachment, attachment_id)
    if att is None or att.request_id != req.id or (att.message.kind == "note" and role != "super_admin"):
        raise not_found("Attachment")
    if req.user_id != p.user.id:
        audit.record(db, request, "help.attachment_opened", actor=p.user, target_type="help_request",
                     target_id=req.id, group_id=req.group_id, details={"name": att.name})
        db.commit()
    return StreamingResponse(get_storage().get_stream(att.s3_key), media_type=att.content_type,
                             headers={"Content-Disposition": f"attachment; filename=\"{safe_name(att.name, 'file')}\"",
                                      "Content-Length": str(att.size_bytes), "X-Content-Type-Options": "nosniff"})


# --- super admins ----------------------------------------------------------------------------------

class StatusChange(BaseModel):
    status: str = Field(pattern="^(in_progress|waiting|resolved|closed)$")


@router.post("/help/{request_id}/status", response_model=HelpOut)
def change_status(request_id: uuid.UUID, body: StatusChange, request: Request,
                  p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    req, role = _visible(db, request_id, p, lock=True)
    old = req.status
    if body.status in ("in_progress", "waiting") and not req.active and req.job_id and db.scalar(
            select(HelpRequest.id).where(HelpRequest.job_id == req.job_id, HelpRequest.id != req.id,
                                         HelpRequest.status.in_(("open", "in_progress", "waiting")))):
        raise conflict("help_open", "Another help request for this document is still being worked on.")
    if helpdesk.set_status(db, req, body.status, p.user, role) is None:
        return _out(db, req, role)
    if req.assignee_id is None and body.status in ("in_progress", "waiting"):
        req.assignee_id, req.assignee_email = p.user.id, p.user.email
    audit.record(db, request, "help.status", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"from": old, "to": body.status})
    helpdesk.notify(db, req, "person")
    db.commit()
    db.refresh(req)
    return _out(db, req, role)


class Assign(BaseModel):
    take: bool  # true: the caller takes it; false: nobody has it


@router.post("/help/{request_id}/assign", response_model=HelpOut)
def assign(request_id: uuid.UUID, body: Assign, request: Request, p: Principal = Depends(require_super_admin),
           db: Session = Depends(get_db)):
    req, role = _visible(db, request_id, p, lock=True)
    new_id, new_email = (p.user.id, p.user.email) if body.take else (None, None)
    if req.assignee_id != new_id:
        req.assignee_id, req.assignee_email = new_id, new_email
        helpdesk.add_message(db, req, p.user, role, "event", "assigned" if body.take else "released")
        if body.take and req.status == "open":
            helpdesk.set_status(db, req, "in_progress", p.user, role)
        audit.record(db, request, "help.assigned", actor=p.user, target_type="help_request", target_id=req.id,
                     group_id=req.group_id, details={"assignee": new_email})
        db.commit()
        db.refresh(req)
    return _out(db, req, role)


@router.get("/admin/help", response_model=list[HelpSummary])
def queue(saved: bool = False, status: str | None = Query(None, max_length=16), kind: str | None = Query(None, max_length=16),
          group_id: uuid.UUID | None = None, q: str | None = Query(None, max_length=200),
          _: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Every request (or the saved ones), newest activity first; the app sorts and filters further."""
    stmt = select(HelpRequest).where(HelpRequest.saved.is_(saved))
    if status in HELP_STATUSES:
        stmt = stmt.where(HelpRequest.status == status)
    if kind in HELP_KINDS:
        stmt = stmt.where(HelpRequest.kind == kind)
    if group_id:
        stmt = stmt.where(HelpRequest.group_id == group_id)
    if q:
        like = f"%{q.lower().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.join(User, User.id == HelpRequest.user_id).where(
            or_(HelpRequest.filename.ilike(like, escape="\\"), User.email.like(like, escape="\\")))
    reqs = db.scalars(stmt.order_by(HelpRequest.updated_at.desc()).limit(1000)).all()
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.id.in_([r.job_id for r in reqs if r.job_id])))}
    return [HelpSummary(**_summary(r, jobs.get(r.job_id))) for r in reqs]


@router.get("/groups/{group_id}/help", response_model=list[HelpSummary])
def group_requests(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    group: Group = group_for_admin(group_id, p, db)
    reqs = db.scalars(select(HelpRequest).where(HelpRequest.group_id == group.id)
                      .order_by(HelpRequest.updated_at.desc()).limit(500)).all()
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.id.in_([r.job_id for r in reqs if r.job_id])))}
    return [HelpSummary(**_summary(r, jobs.get(r.job_id))) for r in reqs]


# --- part B: resolving, corrected files, reopening, saving ------------------------------------------

def _active_elsewhere(db: Session, req: HelpRequest) -> bool:
    return req.job_id is not None and db.scalar(select(HelpRequest.id).where(
        HelpRequest.job_id == req.job_id, HelpRequest.id != req.id,
        HelpRequest.status.in_(("open", "in_progress", "waiting")))) is not None


@router.post("/help/{request_id}/corrected", response_model=HelpOut, status_code=201)
def upload_corrected(request_id: uuid.UUID, request: Request, file: UploadFile = File(...),
                     p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """A corrected PDF made outside the portal becomes a new version of the document, "Corrected by support N".
    It must have the same number of pages; it's checked like any saved version (integrity, veraPDF, and a
    visual comparison with the version it replaces), and failed checks are shown, not blocking."""
    from portal.pdfcheck import receive_pdf
    from portal.storage import job_prefix

    req, role = _visible(db, request_id, p, lock=True)
    job = db.scalar(select(Job).where(Job.id == req.job_id).with_for_update()) if req.job_id else None
    if job is None or job.files_deleted_at is not None:
        raise ApiError(409, "document_gone", "The document's files are no longer kept, so there's nothing to add a version to.")
    rs = settings_store.load(db)
    checked = receive_pdf(file, rs.max_file_mb * 1024 * 1024, rs.max_pages_per_document)
    try:
        if checked.pages != job.pages:
            raise ApiError(422, "page_count", f"The corrected file has {checked.pages} pages; the document has {job.pages}. "
                                              "Corrected files must have the same pages.")
        versions = db.scalars(select(DocumentVersion).where(DocumentVersion.job_id == job.id)).all()
        base = next((v for v in versions if v.number == req.version_number and v.kind != "original"), None) or max(
            (v for v in versions if v.status == "ready" and v.kind != "original" and v.pdf_file_id),
            key=lambda v: v.number, default=None)
        if base is None:
            raise ApiError(409, "no_version", "There's no remediated version to compare the corrected file with.")
        number = max(v.number for v in versions) + 1
        label = f"Corrected by support {sum(1 for v in versions if v.kind == 'corrected') + 1}"
        from portal.models import JobFile
        name = f"{job.filename.rsplit('.', 1)[0]}_corrected_upload_{number}.pdf"
        key = job_prefix(job.group_id, job.user_id, job.id) + f"versions/{number}/{safe_name(name)}"
        size = get_storage().put_file(key, checked.file, "application/pdf")
        src = JobFile(job_id=job.id, kind="corrected_upload", name=name, s3_key=key, size_bytes=size,
                      content_type="application/pdf")
        db.add(src)
        db.flush()
    finally:
        checked.file.close()
    v = DocumentVersion(job_id=job.id, number=number, kind="corrected", label=label, based_on_id=base.id,
                        created_by=p.user.id, created_by_email=p.user.email, status="queued", edits=[],
                        note=f"Uploaded for a help request ({file.filename or 'corrected file'})", source_file_id=src.id)
    db.add(v)
    helpdesk.add_message(db, req, p.user, role, "event", f"corrected:{label}")
    audit.record(db, request, "help.corrected_uploaded", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"job_id": str(job.id), "version": number, "based_on": base.number,
                                                 "pages": checked.pages})
    db.commit()
    db.refresh(req)
    return _out(db, req, role)


class Resolve(BaseModel):
    outcome: str = Field("resolved", pattern="^(resolved|closed)$")
    note: str = Field("", max_length=helpdesk.MAX_BODY)  # what was done; the person sees it
    version_number: int | None = Field(None, ge=0)  # the version that fixes it
    refund_pages: int | None = Field(None, ge=1, le=1_000_000)


@router.post("/help/{request_id}/resolve", response_model=HelpOut)
def resolve(request_id: uuid.UUID, body: Resolve, request: Request,
            p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Finish a request: resolved (usually with a new version) or closed without changes. The note is posted to
    the person; pages can be given back through the usual audited usage correction."""
    from portal import quota, services
    from portal.routers.admin import _actor

    req, role = _visible(db, request_id, p, lock=True)
    if not req.active:
        raise ApiError(409, "help_finished", "This request is already finished.")
    note = body.note.strip()
    if body.outcome == "resolved" and not note:
        raise ApiError(422, "note_required", "Say what was done; the person sees it.")
    job = db.get(Job, req.job_id) if req.job_id else None
    if body.version_number is not None:
        v = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == req.job_id,
                                                    DocumentVersion.number == body.version_number)) if job else None
        if v is None or v.kind == "original" or v.status == "failed":
            raise ApiError(422, "invalid_version", "Choose a version of this document that isn't the original or failed.")
        req.resolved_version_number = v.number
    if body.refund_pages:
        if job is None or body.refund_pages > job.pages_charged:
            raise ApiError(422, "refund_too_large",
                           f"You can give back at most the {job.pages_charged if job else 0} pages the document cost.")
        owner = db.get(User, req.user_id)
        ai_given_back = bool((job.cost or {}).get("ai_refunded"))
        for key in [k for k in (job.charged_services or [job.processing]) if not (ai_given_back and k == job.ai_tier)]:
            if services.get_service(db, key) is None:
                continue  # an AI level deleted since: its pools went with it, so there is nowhere to give pages back
            quota.correct_usage(db, owner, -body.refund_pages, _actor(p, request),
                                comment=f"Help request about {req.filename}", ticket_ref=None, service=key)
        req.pages_refunded = body.refund_pages
    if note:
        helpdesk.add_message(db, req, p.user, role, "message", note)
        req.resolution = note
    helpdesk.set_status(db, req, body.outcome, p.user, role)
    if req.assignee_id is None:
        req.assignee_id, req.assignee_email = p.user.id, p.user.email
    audit.record(db, request, "help.resolved", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"outcome": body.outcome, "version": req.resolved_version_number,
                                                 "pages_refunded": body.refund_pages})
    helpdesk.notify(db, req, "person")
    db.commit()
    db.refresh(req)
    return _out(db, req, role)


@router.post("/help/{request_id}/reopen", response_model=HelpOut)
def reopen(request_id: uuid.UUID, request: Request,
           p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The person reopens a finished request within the reopen window (super admins: any time)."""
    req, role = _visible(db, request_id, p, lock=True)
    out = _out(db, req, role)
    if not out.can_reopen:
        raise ApiError(409, "cant_reopen", "This request can't be reopened. If there's still a problem, ask for help again.")
    if _active_elsewhere(db, req):
        raise conflict("help_open", "Another help request for this document is still being worked on.")
    helpdesk.set_status(db, req, "in_progress", p.user, role, event="reopened")
    req.person_wrote_at = helpdesk._now() if role == "person" else req.person_wrote_at
    audit.record(db, request, "help.reopened", actor=p.user, target_type="help_request", target_id=req.id,
                 group_id=req.group_id, details={"role": role})
    helpdesk.notify(db, req, "staff" if role == "person" else "person")
    db.commit()
    db.refresh(req)
    return _out(db, req, role)


class SaveIn(BaseModel):
    saved: bool


@router.post("/help/{request_id}/save", response_model=HelpOut)
def save_request(request_id: uuid.UUID, body: SaveIn, request: Request, p: Principal = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    """Keep a request past the document's retention (it moves to the Saved view), or let it expire again."""
    req, role = _visible(db, request_id, p, lock=True)
    if req.saved != body.saved:
        req.saved = body.saved
        audit.record(db, request, "help.saved" if body.saved else "help.unsaved", actor=p.user,
                     target_type="help_request", target_id=req.id, group_id=req.group_id, details={})
        db.commit()
        db.refresh(req)
    return _out(db, req, role)

