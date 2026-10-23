"""Submitting PDFs, following jobs, and downloading results."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Integer, Select, cast, func, or_, select, true
from sqlalchemy.orm import Session, object_session

from portal import api_tokens, approval, audit, export, finding_marks, malware, quota, removal, services, settings_store
from portal.db import get_db
from portal.deps import Principal, current_principal, document_principal, require_super_admin
from portal.errors import ApiError, not_found
from portal.export import STAFF_ONLY_FILES
from portal.models import ACTIVE_JOB_STATUSES, ApiTokenDownload, HelpRequest, Job, JobFile, User
from portal.pdfcheck import receive_pdf
from portal.security.ratelimit import per_person
from portal.storage import get_storage, job_prefix, safe_name

router = APIRouter(tags=["jobs"])

FILE_LABELS = {
    "input": "Original",
    "primary": "Remediated PDF",
    "visual_fallback": "Visually exact PDF",
    "report_pdf": "Report (PDF)",
    "report_md": "Report (Markdown)",
    "report_json": "Report (JSON)",
    "verapdf_before": "veraPDF report: before",
    "verapdf_after": "veraPDF report: after",
    "verapdf_after_visual": "veraPDF report: visually exact file",
    "adobe_report": "Adobe accessibility report (Excel)",
    "enhanced": "Improved copy of Adobe's file (PDF)",
    "verapdf_after_enhanced": "veraPDF report: improved copy",
    "music_score": "Music: the whole piece (MusicXML)",
    "music_files": "Music: every line, and the whole piece (MusicXML files, zip)",
    "math_list": "Math: every formula, to read with a screen reader (web page)",
    "math_files": "Math: every formula (MathML files, zip)",
    "math_nemeth": "Math in braille: Nemeth (BRF)",
    "math_ueb": "Math in braille: UEB (BRF)",
}




class JobFileOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    name: str
    size_bytes: int


class JobOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None
    group_id: uuid.UUID | None
    filename: str
    pages: int
    pages_charged: int
    report_only: bool
    processing: str
    ai_tier: str | None
    ai_kind: str | None  # the kind of document within the AI level; None: General
    ai_kind_fallback: str | None  # processed with the level's General instead: "unavailable" or "failed"
    ai_kind_auto: bool  # "Let AI decide": the classifier picks the kind before the document is sent
    ai_kind_pick: dict | None  # what it picked: {kind, label, confidence, used, reason}
    ai_label: str | None  # "Premium AI · Math and science": the level and kind by name (never the model)
    charged_services: list[str]
    external_services: list[str]
    needs_confirmation: bool
    full_review: bool
    ignore_existing: bool  # its own tags ignored, tagged afresh (#396)
    no_ai: bool  # made without AI (an audit, when its AI level is set that way)
    ai_refunded: bool  # the AI tier's pages were given back: every AI call failed, or (ai_unused) none was needed
    ai_unused: bool
    cost: dict | None  # AI spend etc.; admins only
    status: str
    progress: dict | None
    result: dict | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    files_expire_at: datetime | None  # the earlier of the end of retention and the end of the hold, if marked done
    files_deleted: bool
    # Marked done by its owner: the files go at removal_at (the hold gives time to undo it)
    removal_requested: bool = False
    removal_at: datetime | None = None
    removal_hold_hours: int = 24
    deleted: bool = False  # deleted by a super admin (only super admins still see it)
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    files: list[JobFileOut]
    help: dict | None = None  # the current help request: {id, status, unread}; on the owner's own list
    # Super admins only (#313): what the Run details tab shows beyond the cost: the engine's job, when it was sent
    staff: dict | None = None
    # A super admin's re-run (#323): the document it re-ran, and (on the document's own page) its re-runs
    rerun_of: uuid.UUID | None = None
    reruns: list[uuid.UUID] = []
    sent_by_script: str | None = None  # the API token's name, when a script sent it (#518)


def _file_label(job: Job, kind: str) -> str:
    summary = job.result_summary or {}
    if kind == "primary" and (summary.get("metrics") or {}).get("route") == "adobe":
        return "Adobe tagged PDF"
    if kind == "enhanced" and summary.get("improved_label"):
        return f"{summary['improved_label']} (PDF)"
    return FILE_LABELS.get(kind, kind)


def _ai_label(job: Job) -> str | None:
    db = object_session(job)
    if job.no_ai:
        return "No AI"
    if not job.ai_tier or db is None:
        return None
    level = services.get_service(db, job.ai_tier)
    name = level.label if level else job.ai_tier
    if job.ai_kind_auto and not job.ai_kind_pick:
        return f"{name} · Let AI decide"
    return f"{name} · {services.kind_label(db, job.ai_tier, job.ai_kind)}" if job.ai_kind else name


def job_out(job: Job, runtime: settings_store.RuntimeSettings, with_user: bool = False,
            show_cost: bool = False) -> JobOut:
    """show_cost: the viewer is a super admin. The AI's cost and the engine's own reports (STAFF_ONLY_FILES) are theirs
    only."""
    hold = runtime.early_removal_hold_hours
    removal_at = removal.remove_at(job, hold)
    expires = None if job.files_deleted_at else job.created_at + timedelta(days=runtime.file_retention_days)
    return JobOut(
        id=job.id, user_id=job.user_id, user_email=job.user.email if with_user else None, group_id=job.group_id,
        filename=job.filename, pages=job.pages, pages_charged=job.pages_charged, report_only=job.report_only,
        processing=job.processing, ai_tier=job.ai_tier, ai_kind=job.ai_kind, ai_kind_fallback=job.ai_kind_fallback,
        ai_kind_auto=bool(job.ai_kind_auto), ai_kind_pick=job.ai_kind_pick,
        ai_label=_ai_label(job),
        charged_services=list(job.charged_services or []),
        external_services=list(job.external_services or []), needs_confirmation=job.status == "held",
        full_review=job.full_review, ignore_existing=bool(job.ignore_existing), no_ai=bool(job.no_ai),
        ai_refunded=bool((job.cost or {}).get("ai_refunded")),
        ai_unused=bool((job.cost or {}).get("ai_unused")),
        cost=job.cost if show_cost else None,
        staff={"engine_job_id": job.engine_job_id, "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None}
        if show_cost else None,
        rerun_of=job.rerun_of, sent_by_script=job.api_token.name if job.api_token_id and job.api_token else None,
        status=job.status, progress=job.progress,
        result=finding_marks.summary_with_keys(job.result_summary, "review", "findings"), error_code=job.error_code,
        error_message=job.error_message, created_at=job.created_at, finished_at=job.finished_at,
        files_expire_at=min(expires, removal_at) if expires and removal_at else expires,
        files_deleted=job.files_deleted_at is not None,
        removal_requested=job.removal_requested_at is not None, removal_at=removal_at, removal_hold_hours=hold,
        deleted=job.deleted_at is not None, deleted_at=job.deleted_at, deleted_by=job.deleted_by,
        files=[] if job.files_deleted_at else [
            JobFileOut(id=f.id, kind=f.kind, label=_file_label(job, f.kind), name=f.name,
                       size_bytes=f.size_bytes) for f in job.files
            if not f.kind.startswith(("tagmap_", "reviewed_", "corrected_"))
            and (show_cost or f.kind != "report_json")])  # (the Markdown report stays listed: the Report tab is shown
            # when there is one, and reads it through /jobs/{id}/report)


class UploadResult(BaseModel):
    filename: str
    accepted: bool
    job: JobOut | None = None
    error: dict | None = None


def _visible_job(db: Session, job_id: uuid.UUID, p: Principal) -> Job:
    """Owner, an admin of the job's group, or a super admin. Others get 404."""
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job")
    if p.is_super_admin:
        return job
    # A deleted document is gone for everyone but super admins (its record stays for reports)
    if job.deleted_at is None and (job.user_id == p.user.id or (job.group_id and p.can_admin_group(job.group_id))):
        return job
    raise not_found("Job")


class KindChoice(BaseModel):
    key: str
    label: str
    description: str | None


class ServiceChoice(BaseModel):
    """A service the signed-in user can pick at upload, with what to tell them about it."""
    key: str
    kind: str
    label: str
    description: str | None
    external: bool
    notice: str | None            # shown when the service is picked (None: no notice for this service)
    confirm_before_send: bool
    max_pages_per_job: int | None
    remaining_pages: int | None   # None = unlimited
    is_default: bool
    allow_full_review: bool       # AI tiers: people may ask for every figure to be reviewed
    ai_use: str | None            # processing services: "full" (AI helps throughout) or "alt_text"
                                  # (optional AI alt text for figures, in a separate copy)
    kinds: list[KindChoice] = []  # AI levels: kinds of document, General first (never model names)
    audit_ai: str | None = None   # AI levels: an audit with this level is free, counts its pages, or uses no AI


@router.get("/services", response_model=list[ServiceChoice])
def my_services(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Services this user may use now (switched on globally and for their group, and granted to them)."""
    out = []
    user = p.user
    classifier = services.classifier_for(db, user.group)
    for s in services.all_services(db):
        if not services.access(db, user, s.key).ok:
            continue
        gs = user.group.service(s.key)
        out.append(ServiceChoice(
            key=s.key, kind=s.kind, label=s.label, description=s.description, external=s.external,
            notice=(s.notice_text or None) if s.notice_enabled else None, confirm_before_send=s.confirm_before_send,
            max_pages_per_job=s.max_pages_per_job, remaining_pages=services.remaining(user, s.key, s),
            is_default=bool(gs and gs.is_default), allow_full_review=s.allow_full_review,
            ai_use=services.ai_use(s) if s.kind == "processing" else None,
            kinds=_kind_choices(db, s.key, classifier) if s.kind == "ai_tier" else [],
            audit_ai=s.audit_ai if s.kind == "ai_tier" else None))
    return out


def _kind_choices(db: Session, level: str, classifier) -> list[KindChoice]:
    """General first, then "Let AI decide" (when the group can use the classifier), then the level's kinds."""
    ks = services.kinds(db, level)
    out = [KindChoice(key=services.GENERAL, label="General", description=None)]
    if ks and classifier:
        where = " with an outside service" if classifier[0] == "jev" else ""
        out.append(KindChoice(key=services.AUTO, label="Let AI decide",
                              description=f"The first pages' text is read{where} to pick the kind; General is used "
                                          "when it isn't sure."))
    return out + [KindChoice(key=k.key, label=k.label, description=k.description) for k in ks]


class UploadOptions(BaseModel):
    """What the upload form offers, as the person's group sets it."""
    ai_mode: str              # "choose": people pick the AI level and kind; "fixed": the group's are used
    ai_level: str | None      # fixed: the level
    ai_kind: str | None       # fixed: the kind (None: General)
    show_external: bool       # mark the options that send documents to an outside provider
    malware_scan: bool = False  # uploads are scanned for malware before they're accepted (#455), so say so (#560)


@router.get("/upload-options", response_model=UploadOptions)
def upload_options(p: Principal = Depends(current_principal)):
    g = p.user.group
    if g is None:
        return UploadOptions(ai_mode="choose", ai_level=None, ai_kind=None, show_external=False,
                             malware_scan=malware.enabled())
    fixed = g.ai_mode == "fixed" and bool(g.ai_level)
    return UploadOptions(ai_mode="fixed" if fixed else "choose", ai_level=g.ai_level if fixed else None,
                         ai_kind=g.ai_kind if fixed else None, show_external=g.show_external,
                         malware_scan=malware.enabled())


def _pick_tier(db: Session, user, requested: str | None) -> str | None:
    """The AI tier for an upload: the one asked for, else the group's default tier if the user can use it,
    else none (the engine's default AI)."""
    if requested:
        s = services.get_service(db, requested)
        if s is None or s.kind != "ai_tier":
            raise ApiError(422, "unknown_service", "That AI level doesn't exist.")
        a = services.access(db, user, requested)
        if not a.ok:
            raise ApiError(403, a.code, a.message)
        return requested
    for gs in user.group.services if user.group else []:
        if gs.is_default and gs.service.kind == "ai_tier" and services.access(db, user, gs.service_key).ok:
            return gs.service_key
    return None


def _pick_kind(db: Session, tier: str | None, requested: str | None, group=None) -> str | None:
    """The kind of document within the AI level: one the level offers, "auto" (the classifier picks it before the
    document is sent), else General (None)."""
    if not tier or not requested or requested == services.GENERAL:
        return None
    if requested == services.AUTO:
        if not services.kinds(db, tier) or services.classifier_for(db, group) is None:
            raise ApiError(422, "classifier_unavailable", "Letting AI decide the kind isn't available here.")
        return services.AUTO
    if requested not in {k.key for k in services.kinds(db, tier)}:
        raise ApiError(422, "unknown_kind", "That kind of document isn't offered with this AI level.")
    return requested


@dataclass
class _Plan:
    """How one file is processed and paid for."""
    tier: str | None
    kind: str | None
    full_review: bool
    keys: list[str]          # every service the document uses
    charge_keys: list[str]   # the services its pages are counted against (none: free)
    hold: bool
    external: list[str]
    no_ai: bool
    auto: bool = False       # the classifier picks the kind before the document is sent


def _per_file(raw: str | None, count: int) -> dict[int, dict]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out = {}
        for k, v in data.items():
            i = int(k)
            if not 0 <= i < count or not isinstance(v, dict) or set(v) - {"ai_tier", "ai_kind"} or not all(
                    x is None or (isinstance(x, str) and len(x) <= 64) for x in v.values()):
                raise ValueError
            out[i] = v
        return out
    except (ValueError, TypeError, AttributeError):
        raise ApiError(422, "bad_per_file", "The choices for single files couldn't be read.")


def _plan(db: Session, user, chosen, processing: str, report_only: bool, ai_alt_text: bool, full_review: bool,
          confirm: bool, ai_tier: str | None, ai_kind: str | None) -> _Plan:
    """The AI level and kind (the group's, when it sets one for everything), what the pages are counted against
    and whether the file waits for confirmation. An audit follows its AI level's setting: free, counted against
    the level, or made without AI."""
    group = user.group
    if group is not None and group.ai_mode == "fixed" and group.ai_level:
        ai_tier, ai_kind = group.ai_level, group.ai_kind
    wants_ai = services.ai_use(chosen) == "full" or ai_alt_text
    tier = _pick_tier(db, user, ai_tier) if wants_ai else None
    kind = _pick_kind(db, tier, ai_kind, group)
    no_ai = False
    charge_keys = [processing] + ([tier] if tier else [])
    if report_only:
        audit_ai = services.get_service(db, tier).audit_ai if tier else "free"
        if audit_ai == "off":
            tier, kind, no_ai = None, None, True
        charge_keys = [tier] if audit_ai == "charge" else []
    full_review = full_review and wants_ai and not no_ai
    if full_review and not services.full_review_allowed(db, tier):
        raise ApiError(422, "full_review_not_allowed", "Reviewing every figure isn't available with this AI level.")
    keys = [processing] + ([tier] if tier else [])
    hold = any(services.get_service(db, k).confirm_before_send for k in keys) and not confirm and not report_only
    external = [k for k in keys if services.get_service(db, k).external]
    auto = kind == services.AUTO
    return _Plan(tier, None if auto else kind, full_review, keys, charge_keys, hold, external, no_ai, auto)


_WAITING_WORDS = {"held": "waiting for you to confirm it", "queued": "waiting its turn",
                  "submitted": "processing", "processing": "processing"}


def _refuse_a_second_copy(db: Session, user_id: uuid.UUID, sha256: str, processing: str, pl: _Plan,
                          report_only: bool, name: str) -> None:
    """The same file, with the same choices, already waiting or in progress for this person: refuse the copy, as a
    second one would only use the pages again (#560: an upload that took a while, the page refreshed, the file sent
    again). The same file with other choices goes ahead: comparing AI levels is a real use."""
    same = db.scalar(select(Job).where(
        Job.user_id == user_id, Job.sha256 == sha256, Job.status.in_(ACTIVE_JOB_STATUSES), Job.deleted_at.is_(None),
        Job.processing == processing, Job.report_only == report_only,
        Job.ai_tier.is_(None) if pl.tier is None else Job.ai_tier == pl.tier,
        Job.ai_kind_auto == pl.auto,
        # "Let AI decide" fills in the kind later, so only a kind chosen at upload is compared
        true() if pl.auto else (Job.ai_kind.is_(None) if pl.kind is None else Job.ai_kind == pl.kind),
    ).order_by(Job.created_at.desc()).limit(1))
    if same is not None:
        raise ApiError(409, "already_in_progress",
                       f"{name} is already in Your documents with the same choices, {_WAITING_WORDS[same.status]}, "
                       "so it wasn't sent again.")


@router.post("/jobs", response_model=list[UploadResult], dependencies=[Depends(per_person("upload", "submit"))])
def upload(request: Request, files: list[UploadFile] = File(...), report_only: bool = Form(False),
           processing: str = Form(services.STANDARD, max_length=64), ai_tier: str | None = Form(None, max_length=64),
           ai_kind: str | None = Form(None, max_length=40),
           confirm: bool = Form(False), full_review: bool = Form(False), ai_alt_text: bool = Form(True),
           ignore_existing: bool = Form(False),
           per_file: str | None = Form(None, max_length=20_000),
           p: Principal = Depends(document_principal("submit")), db: Session = Depends(get_db)):
    """Submit one or more PDFs. Each file is accepted or refused on its own.

    processing picks the service. ai_alt_text: for a service whose AI only writes alt text (Adobe),
    whether to use it (default yes). When it asks for confirmation before documents are sent, files are
    accepted as held (nothing reserved) until confirmed with POST /jobs/{id}/confirm, unless confirm
    is already true.

    per_file: JSON {"<index of the file>": {"ai_tier": ..., "ai_kind": ...}} to give some files their own AI level
    and kind. When the group sets one AI level for everything, what's asked for is ignored.

    With a script's API token (#518), the token's processing service, AI level and kind are used, whatever is sent,
    and documents are confirmed only if the token says so."""
    runtime = settings_store.load(db)
    if p.token is not None:
        processing, ai_tier, ai_kind, per_file, confirm = (p.token.processing, p.token.ai_tier, p.token.ai_kind, None,
                                                           p.token.auto_confirm)
    if p.user.group_id is None:
        raise ApiError(422, "no_group", "Your account isn't in a group, so it has no pages to use.")
    approval.require_approved(p.user)
    chosen = services.get_service(db, processing)
    if chosen is None or chosen.kind != "processing":
        raise ApiError(422, "unknown_service", "That processing option doesn't exist.")
    a = services.access(db, p.user, processing)
    if not a.ok:
        raise ApiError(403, a.code, a.message)
    if report_only and chosen.engine_route != services.STANDARD:
        # A report needs nothing from another provider: it's always made here, and costs no pages.
        processing, chosen = services.STANDARD, services.get_service(db, services.STANDARD)
    overrides = _per_file(per_file, len(files))
    plans: dict[tuple, _Plan] = {}

    def plan_for(i: int) -> _Plan:
        o = overrides.get(i, {})
        req = (o.get("ai_tier", ai_tier), o.get("ai_kind", ai_kind))
        if req not in plans:
            plans[req] = _plan(db, p.user, chosen, processing, report_only, ai_alt_text, full_review, confirm, *req)
        return plans[req]

    for i in [-1, *overrides]:  # refuse a bad choice before any file is taken
        plan_for(i)
    if len(files) > runtime.max_files_per_upload:
        raise ApiError(413, "too_many_files", f"Upload at most {runtime.max_files_per_upload} files at a time.")
    storage = get_storage()
    results: list[UploadResult] = []
    for index, upload_file in enumerate(files):
        name = (upload_file.filename or "document.pdf")[:255]
        try:
            checked = receive_pdf(upload_file, runtime.max_file_mb * 1024 * 1024,
                                        runtime.max_pages_per_document)
        except ApiError as e:
            results.append(UploadResult(filename=name, accepted=False, error=e.detail))
            continue
        try:
            malware.check(db, request, p.user, checked.file, name, "document")  # with MALWARE_SCAN=clamav (#455)
        except ApiError as e:
            checked.file.close()
            results.append(UploadResult(filename=name, accepted=False, error=e.detail))
            continue
        try:
            job_id = uuid.uuid4()
            # Lock the user row so the waiting-documents limit holds under parallel uploads.
            # (How many run at once is the worker's job: see max_concurrent_jobs_per_user.)
            db.scalar(select(User).where(User.id == p.user.id).with_for_update())
            pl = plan_for(index)
            _refuse_a_second_copy(db, p.user.id, checked.sha256, processing, pl, report_only, name)
            if quota.active_jobs(db, p.user.id) >= runtime.max_queued_jobs_per_user:
                raise ApiError(429, "too_many_jobs",
                               f"You already have {runtime.max_queued_jobs_per_user} documents waiting or in progress. "
                               "Submit more when some have finished.")
            # Held for the person's confirmation: a service that asks for it, or a signed PDF whose signature the
            # remediated copy won't keep (#339; a check only changes nothing, so it isn't held)
            held = [r for r, on in (("service", pl.hold), ("signed", checked.signed and not report_only)) if on]
            charged = checked.pages if pl.charge_keys else 0
            if held:
                quota.check_access(db, p.user, pl.keys, checked.pages)  # refuse now what can't be confirmed later
                charged = 0
            elif charged:
                quota.reserve(db, p.user, charged, job_id, pl.charge_keys)
            job = Job(id=job_id, user_id=p.user.id, group_id=p.user.group_id, filename=name, pages=checked.pages,
                      pages_charged=charged, size_bytes=checked.size, sha256=checked.sha256, report_only=report_only,
                      processing=processing, ai_tier=pl.tier, ai_kind=pl.kind,
                      charged_services=pl.charge_keys if charged else [], external_services=pl.external,
                      status="held" if held else "queued", full_review=pl.full_review, no_ai=pl.no_ai,
                      ignore_existing=ignore_existing and not report_only,  # a check only changes no tags
                      ai_kind_auto=pl.auto, progress={"held": held} if held else None,
                      api_token_id=p.token.id if p.token else None)
            db.add(job)
            db.flush()
            key = job_prefix(job.group_id, job.user_id, job.id) + "input/" + safe_name(name)
            storage.put_file(key, checked.file, "application/pdf")
            db.add(JobFile(job_id=job.id, kind="input", name=name, s3_key=key, size_bytes=checked.size,
                           content_type="application/pdf"))
            audit.record(db, request, "job.submitted", actor=p.user, target_type="job", target_id=job.id,
                         group_id=job.group_id, details={"filename": name, "pages": checked.pages,
                                                         "charged": charged, "report_only": report_only,
                                                         "processing": processing, "ai_tier": pl.tier,
                                                         "ai_kind": services.AUTO if pl.auto else pl.kind,
                                                         "no_ai": pl.no_ai,
                                                         "external": pl.external, "held": bool(held),
                                                         **({"signed": True} if checked.signed else {}),
                                                         **({"api_token": p.token.name} if p.token else {})})
            db.commit()
            db.refresh(job)
            results.append(UploadResult(filename=name, accepted=True, job=job_out(job, runtime)))
        except ApiError as e:
            db.rollback()
            results.append(UploadResult(filename=name, accepted=False, error=e.detail))
        finally:
            checked.file.close()
    return results


@router.get("/jobs", response_model=list[JobOut])
def my_jobs(limit: int = Query(50, ge=1, le=500), before: datetime | None = None,
            p: Principal = Depends(document_principal("status")), db: Session = Depends(get_db)):
    stmt = select(Job).where(Job.user_id == p.user.id, Job.deleted_at.is_(None))
    if before:
        stmt = stmt.where(Job.created_at < before)
    jobs = db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit)).all()
    return _with_help(db, jobs, settings_store.load(db))


class DocumentsPage(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int
    active: int  # documents of theirs still waiting or in progress (any filter), so the app knows to refresh


STATUS_GROUPS = {
    "waiting": ("queued",), "in_progress": ("submitted", "processing"), "done": ("completed",),
    "failed": ("failed",), "canceled": ("canceled",), "held": ("held",),
}
SORTS = {"created_at": Job.created_at, "filename": func.lower(Job.filename), "pages": Job.pages,
         "result": Job.result_summary["compliant_after"].as_boolean()}
PAGE_SIZES = (25, 50, 100, 200)


@router.get("/documents", response_model=DocumentsPage)
def my_documents(q: str = Query("", max_length=200), status: str | None = Query(None, max_length=20),
                 result: str | None = Query(None, max_length=20), service: str | None = Query(None, max_length=64),
                 audit: bool | None = None, since: datetime | None = None, until: datetime | None = None,
                 sort: str = Query("-created_at", max_length=20), page: int = Query(1, ge=1, le=100_000),
                 page_size: int = Query(25, ge=1, le=200), not_downloaded: bool = False,
                 via: str | None = Query(None, pattern="^(script|web)$"),
                 p: Principal = Depends(document_principal("status")), db: Session = Depends(get_db)):
    """The signed-in person's documents, a page at a time: search by file name, filter by status, result,
    processing service or AI level, audit only and date; sort by date, name, pages or result.

    not_downloaded (a script's API token, #518): only the documents this token hasn't downloaded yet, so a polling
    job asks for status=done&not_downloaded=true and gets just the new ones."""
    scope = [Job.user_id == p.user.id]
    if not_downloaded and p.token is not None:
        scope.append(Job.id.not_in(select(ApiTokenDownload.job_id).where(ApiTokenDownload.token_id == p.token.id)))
    return documents_page(db, scope, q=q, status=status, result=result, service=service,
                          audit=audit, since=since, until=until, sort=sort, page=page, page_size=page_size, via=via)


def documents_query(scope: list, *, q: str, status: str | None, result: str | None, service: str | None,
                    audit: bool | None, since: datetime | None, until: datetime | None, sort: str,
                    people: bool = False, via: str | None = None) -> tuple[list, list]:
    """The conditions and the order of the documents in `scope` (a person's, a group's), searched, filtered and sorted
    the same way wherever they are listed, and wherever a document's Previous and Next follow the list. With `people`,
    the list is an admin's view of other people's documents: the search also matches the person's email, and the list
    can be sorted by person."""
    where = [*scope, Job.deleted_at.is_(None)]
    if q.strip():
        like = "%" + q.strip().lower().replace("%", r"\%").replace("_", r"\_") + "%"
        by_name = func.lower(Job.filename).like(like, escape="\\")
        where.append(or_(by_name, Job.user_id.in_(select(User.id).where(func.lower(User.email).like(like, escape="\\"))))
                     if people else by_name)
    if status:
        if status not in STATUS_GROUPS:
            raise ApiError(422, "bad_filter", "Unknown status.")
        where.append(Job.status.in_(STATUS_GROUPS[status]))
    passes = Job.result_summary["compliant_after"].as_boolean()
    findings = cast(Job.result_summary["review"]["findings_total"].as_string(), Integer)
    if result == "passes":
        where += [Job.status == "completed", Job.report_only.is_(False), passes.is_(True)]
    elif result == "needs_work":
        where += [Job.status == "completed", Job.report_only.is_(False), passes.is_not(True)]
    elif result == "review":
        where += [Job.status == "completed", findings > 0]
    elif result == "help":
        where.append(Job.id.in_(select(HelpRequest.job_id).where(HelpRequest.status.in_(("open", "in_progress", "waiting")))))
    elif result:
        raise ApiError(422, "bad_filter", "Unknown result.")
    if service:
        where.append(or_(Job.processing == service, Job.ai_tier == service))
    if audit is not None:
        where.append(Job.report_only.is_(audit))
    if since:
        where.append(Job.created_at >= since)
    if until:
        where.append(Job.created_at < until)
    if via == "script":  # sent with an API token (#518)
        where.append(Job.api_token_id.is_not(None))
    elif via == "web":
        where.append(Job.api_token_id.is_(None))
    elif via:
        raise ApiError(422, "bad_filter", "Unknown way of sending.")
    key, desc = sort.lstrip("-"), sort.startswith("-")
    sorts = {**SORTS, "user": select(func.lower(User.email)).where(User.id == Job.user_id).scalar_subquery()} if people \
        else SORTS
    if key not in sorts:
        raise ApiError(422, "bad_sort", "Unknown sort.")
    # The id last: documents sent together share a time, and the order has to be the same on every page
    order = [sorts[key].desc().nulls_last() if desc else sorts[key].asc().nulls_last(), Job.created_at.desc(), Job.id]
    return where, order


def documents_page(db: Session, scope: list, *, q: str, status: str | None, result: str | None, service: str | None,
                   audit: bool | None, since: datetime | None, until: datetime | None, sort: str, page: int,
                   page_size: int, people: bool = False, show_cost: bool = False, via: str | None = None) -> DocumentsPage:
    """One page of the documents in `scope` (see documents_query). With `people`, each says whose it is."""
    where, order = documents_query(scope, q=q, status=status, result=result, service=service, audit=audit, since=since,
                                   until=until, sort=sort, people=people, via=via)
    total = db.scalar(select(func.count()).select_from(Job).where(*where)) or 0
    size = min(page_size, max(PAGE_SIZES))
    jobs = db.scalars(select(Job).where(*where).order_by(*order).limit(size).offset((page - 1) * size)).all()
    active = db.scalar(select(func.count()).select_from(Job).where(
        *scope, Job.status.in_(("queued", "submitted", "processing")))) or 0
    return DocumentsPage(items=_with_help(db, jobs, settings_store.load(db), with_user=people, show_cost=show_cost),
                         total=total, page=page, page_size=size, active=active)


class Neighbour(BaseModel):
    id: uuid.UUID
    filename: str


class Neighbours(BaseModel):
    """A document's place in a list, for its page's Previous and Next. position: 1-based, None when the document
    isn't in the list (it no longer matches its filters); previous and next: None at the ends."""
    position: int | None
    total: int
    previous: Neighbour | None
    next: Neighbour | None


def neighbours(db: Session, job_id: uuid.UUID, stmt: Select, order: list, id_col=Job.id,
               name_col=Job.filename) -> Neighbours:
    """The documents either side of `job_id` in the list `stmt` (its conditions and joins; its columns are replaced)
    sorted by `order`: the list's own query, so the order can't differ from what it showed, and across its pages (the
    web app has only one page of it)."""
    numbered = stmt.with_only_columns(id_col.label("id"), name_col.label("filename"),
                                      func.row_number().over(order_by=order).label("n")).subquery()
    total = db.scalar(select(func.count()).select_from(numbered)) or 0
    at = db.scalar(select(numbered.c.n).where(numbered.c.id == job_id))
    if at is None:
        return Neighbours(position=None, total=total, previous=None, next=None)
    either = {n: Neighbour(id=i, filename=f) for i, f, n in db.execute(
        select(numbered.c.id, numbered.c.filename, numbered.c.n).where(numbered.c.n.in_((at - 1, at + 1))))}
    return Neighbours(position=at, total=total, previous=either.get(at - 1), next=either.get(at + 1))


@router.get("/documents/neighbours/{job_id}", response_model=Neighbours)
def my_document_neighbours(job_id: uuid.UUID, q: str = Query("", max_length=200),
                           status: str | None = Query(None, max_length=20), result: str | None = Query(None, max_length=20),
                           service: str | None = Query(None, max_length=64), audit: bool | None = None,
                           since: datetime | None = None, until: datetime | None = None,
                           sort: str = Query("-created_at", max_length=20),
                           via: str | None = Query(None, pattern="^(script|web)$"),
                           p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Previous and Next on a document's page, opened from My documents: the same filters and sort as the list."""
    where, order = documents_query([Job.user_id == p.user.id], q=q, status=status, result=result, service=service,
                                   audit=audit, since=since, until=until, sort=sort, via=via)
    return neighbours(db, job_id, select(Job).where(*where), order)


def _with_help(db: Session, jobs: list[Job], runtime: settings_store.RuntimeSettings, with_user: bool = False,
               show_cost: bool = False) -> list[JobOut]:
    """The documents, each with its current help request (the active one, else the latest). "Unread" is the
    owner's own: an admin looking at someone else's documents hasn't a reply waiting for them there."""
    helps: dict = {}
    for r in db.scalars(select(HelpRequest).where(HelpRequest.job_id.in_([j.id for j in jobs]))
                        .order_by(HelpRequest.created_at)):
        if r.job_id not in helps or r.active:
            helps[r.job_id] = {"id": str(r.id), "status": r.status, "unread": r.unread_for_person and not with_user}
    out = []
    for j in jobs:
        o = job_out(j, runtime, with_user=with_user, show_cost=show_cost)
        o.help = helps.get(j.id)
        out.append(o)
    return out


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_detail(job_id: uuid.UUID, p: Principal = Depends(document_principal("status")), db: Session = Depends(get_db)):
    job = _visible_job(db, job_id, p)
    # Cost is for super admins only: what groups are charged isn't the per-page cost
    out = job_out(job, settings_store.load(db), with_user=job.user_id != p.user.id, show_cost=p.is_super_admin)
    out.reruns = list(db.scalars(select(Job.id).where(Job.rerun_of == job.id, Job.deleted_at.is_(None))
                                 .order_by(Job.created_at)))
    return out


def _send_held(db: Session, request: Request, user: User, job: Job) -> None:
    """A held document's pages reserved and it queued, or OverQuota (nothing changed). The caller commits."""
    keys = [job.processing] + ([job.ai_tier] if job.ai_tier else [])
    quota.reserve(db, user, job.pages, job.id, keys)
    job.pages_charged = job.pages
    job.charged_services = keys
    job.status, job.progress = "queued", None
    audit.record(db, request, "job.confirmed", actor=user, target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"pages": job.pages, "services": keys})


@router.post("/jobs/{job_id}/confirm", response_model=JobOut)
def confirm_job(job_id: uuid.UUID, request: Request, p: Principal = Depends(document_principal("submit")),
                db: Session = Depends(get_db)):
    """Send a held document: its pages are reserved now and it joins the queue."""
    approval.require_approved(p.user)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.user_id != p.user.id:
        raise not_found("Job")
    if job.status != "held":
        raise ApiError(409, "not_held", "This document isn't waiting for confirmation.")
    _send_held(db, request, p.user, job)
    db.commit()
    return job_out(job, settings_store.load(db))


class ConfirmMany(BaseModel):
    job_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    mode: str = Field("all", pattern="^(all|fit)$")  # all or none, or the first ones that fit, in upload order
    dry_run: bool = False  # what would happen, nothing reserved: for the upload form's figures


class ConfirmManyOut(BaseModel):
    sent: list[uuid.UUID]          # sent (or, in a dry run, that would be)
    held: list[uuid.UUID]          # still waiting
    needed_pages: int              # all of them
    sent_pages: int
    remaining_pages: int | None    # pages left on the tightest of their services before sending (None: unlimited)
    message: str | None = None     # why the rest didn't fit


@router.post("/jobs/confirm", response_model=ConfirmManyOut)
def confirm_many(body: ConfirmMany, request: Request, p: Principal = Depends(document_principal("submit")),
                 db: Session = Depends(get_db)):
    """Send several held documents at once (#494), in one transaction, so the batch is judged against the pages
    left as a whole and two tabs can't spend the same pages: `all` sends every one or none (403 over_quota, with
    the pages needed and left), `fit` sends them in upload order until one doesn't fit and leaves the rest held.
    With dry_run nothing is reserved: the answer is what would happen, from the same checks."""
    approval.require_approved(p.user)
    jobs = db.scalars(select(Job).where(Job.id.in_(body.job_ids), Job.user_id == p.user.id, Job.status == "held")
                      .order_by(Job.created_at, Job.id).with_for_update()).all()
    if not jobs:
        raise ApiError(409, "not_held", "None of these documents is waiting for confirmation.")
    keys = sorted({k for j in jobs for k in [j.processing] + ([j.ai_tier] if j.ai_tier else [])})
    lefts = [services.remaining(p.user, k, s) for k in keys if (s := services.get_service(db, k)) is not None]
    left = min((x for x in lefts if x is not None), default=None)
    needed = sum(j.pages for j in jobs)
    sent, message = [], None
    for job in jobs:
        try:
            with db.begin_nested():  # one savepoint per document: a refusal undoes only its own reservation
                _send_held(db, request, p.user, job)
        except quota.OverQuota as e:
            message = e.detail.get("message")
            break
        sent.append(job)
    # The answer is made before any rollback: rolled back, the rows can't be read again as this person
    out = ConfirmManyOut(sent=[j.id for j in sent], held=[j.id for j in jobs if j not in sent], needed_pages=needed,
                         sent_pages=sum(j.pages for j in sent), remaining_pages=left, message=message)
    if body.dry_run or (body.mode == "all" and len(sent) < len(jobs)):
        db.rollback()
        if not body.dry_run:
            raise quota.OverQuota(
                f"These {len(jobs)} documents need {needed:,} pages and there are {left or 0:,} left: none was "
                "sent. Send the ones that fit, or fewer at a time.", "batch", left or 0, needed, None,
                keys[0] if keys else services.STANDARD)
    else:
        db.commit()
    return out


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.user_id != p.user.id:
        raise not_found("Job")
    if job.status not in ("queued", "held"):
        raise ApiError(409, "not_cancelable", "Only documents still waiting in the queue can be canceled.")
    quota.release(db, job, "canceled")
    job.status = "canceled"
    job.finished_at = datetime.now(job.created_at.tzinfo)
    audit.record(db, request, "job.canceled", actor=p.user, target_type="job", target_id=job.id,
                 group_id=job.group_id)
    db.commit()
    return job_out(job, settings_store.load(db))


# --- re-running (super admins) -----------------------------------------------------------------------

class Rerun(BaseModel):
    charge: bool = False  # hold the owner's pages again, as a new upload would
    reason: str | None = Field(None, max_length=500)
    ignore_existing: bool | None = None  # tag it afresh from the page (#396); left out: as the original was run


@router.post("/jobs/{job_id}/rerun", response_model=JobOut, status_code=201)
def rerun(job_id: uuid.UUID, body: Rerun, request: Request, p: Principal = Depends(require_super_admin),
          db: Session = Depends(get_db)):
    """Send a document through again, whatever became of it (#323): a new document for the same owner, with the
    same options and the original file; the old one, its versions and edits stay as they are. Its pages are
    charged again only when asked to; otherwise it's support's own re-run and costs the owner nothing."""
    import tempfile

    from portal.queues import job_services

    old = db.scalar(select(Job).where(Job.id == job_id))
    if old is None or old.deleted_at is not None:
        raise not_found("Job")
    original = next((f for f in old.files if f.kind == "input"), None)
    if old.files_deleted_at is not None or original is None:
        raise ApiError(410, "files_deleted", "This document's file has been deleted, so it can't be run again.")
    owner = old.user
    new_id = uuid.uuid4()
    keys = job_services(old) if body.charge and not old.report_only else []
    if keys:
        quota.reserve(db, owner, old.pages, new_id, keys)  # refused, as an upload would be, if they have too few
    job = Job(id=new_id, user_id=owner.id, group_id=old.group_id, filename=old.filename, pages=old.pages,
              pages_charged=old.pages if keys else 0, size_bytes=old.size_bytes, sha256=old.sha256,
              report_only=old.report_only, processing=old.processing, ai_tier=old.ai_tier, ai_kind=old.ai_kind,
              charged_services=keys, external_services=list(old.external_services or []), status="queued",
              full_review=old.full_review, no_ai=old.no_ai, ai_kind_auto=old.ai_kind_auto, rerun_of=old.id,
              ignore_existing=(old.ignore_existing if body.ignore_existing is None else body.ignore_existing)
              and not old.report_only)
    db.add(job)
    db.flush()
    storage = get_storage()
    key = job_prefix(job.group_id, job.user_id, job.id) + "input/" + safe_name(old.filename)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
        storage.get_to_file(original.s3_key, tmp)
        tmp.seek(0)
        storage.put_file(key, tmp, "application/pdf")
    db.add(JobFile(job_id=job.id, kind="input", name=original.name, s3_key=key, size_bytes=original.size_bytes,
                   content_type="application/pdf"))
    audit.record(db, request, "job.rerun", actor=p.user, target_type="job", target_id=job.id, group_id=job.group_id,
                 details={"rerun_of": str(old.id), "previous_status": old.status, "charged": job.pages_charged,
                          "reason": body.reason or None, "ignore_existing": job.ignore_existing})
    db.commit()
    db.refresh(job)
    return job_out(job, settings_store.load(db))


# --- removing files early ----------------------------------------------------------------------------

class JobIds(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=500)


class AdminRemoval(JobIds):
    delete: bool = False  # True: delete the documents (files now; gone from document lists, kept for reports)
    reason: str = Field(..., min_length=3, max_length=500)


class RemovalResult(BaseModel):
    done: list[str]
    skipped: list[dict]  # {id, reason, message}


def _own_job_for_update(db: Session, job_id: uuid.UUID, p: Principal) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.user_id != p.user.id or job.deleted_at is not None:
        raise not_found("Job")
    return job


@router.post("/jobs/{job_id}/remove-early", response_model=JobOut)
def remove_early(job_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    """The owner is done with a finished document: its files go when the hold ends."""
    job = _own_job_for_update(db, job_id, p)
    removal.request_removal(db, request, job, p.user)
    db.commit()
    return job_out(job, settings_store.load(db))


@router.post("/jobs/remove-early", response_model=RemovalResult)
def remove_early_many(body: JobIds, request: Request, p: Principal = Depends(current_principal),
                      db: Session = Depends(get_db)):
    """Several of the owner's documents at once; those that can't be marked are listed with the reason."""
    ids = removal.parse_ids(body.ids)
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.id.in_(ids), Job.user_id == p.user.id,
                                                          Job.deleted_at.is_(None)).with_for_update())}
    done, skipped = [], []
    for i in ids:
        job = jobs.get(i)
        if job is None:
            skipped.append({"id": str(i), "reason": "not_found", "message": "Document not found."})
            continue
        try:
            removal.request_removal(db, request, job, p.user)
            done.append(str(i))
        except ApiError as e:
            skipped.append({"id": str(i), **{"reason": e.detail["error"], "message": e.detail["message"]}})
    db.commit()
    return RemovalResult(done=done, skipped=skipped)


@router.post("/jobs/{job_id}/keep", response_model=JobOut)
def keep(job_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
         db: Session = Depends(get_db)):
    """Undo "I'm done with it" while the hold lasts: the files are kept for the normal period."""
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.deleted_at is not None or not (job.user_id == p.user.id or p.is_super_admin):
        raise not_found("Job")
    removal.cancel_removal(db, request, job, p.user)
    db.commit()
    return job_out(job, settings_store.load(db), with_user=job.user_id != p.user.id)


@router.post("/admin/jobs/remove", response_model=RemovalResult)
def admin_remove(body: AdminRemoval, request: Request, p: Principal = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    """Super admins: remove documents' files now, or delete the documents. Documents still waiting or in progress
    are skipped."""
    ids = removal.parse_ids(body.ids)
    jobs = list(db.scalars(select(Job).where(Job.id.in_(ids)).with_for_update()))
    found = {j.id for j in jobs}
    result = removal.admin_remove(db, request, jobs, p.user, delete=body.delete, reason=body.reason.strip())
    result["skipped"] += [{"id": str(i), "reason": "not_found", "message": "Document not found."}
                          for i in ids if i not in found]
    db.commit()
    return RemovalResult(**result)


def _file(db: Session, job: Job, file_id: uuid.UUID) -> JobFile:
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
    f = next((f for f in job.files if f.id == file_id), None)
    if f is None:
        raise not_found("File")
    return f


def _mark_downloaded(db: Session, p: Principal, job: Job) -> None:
    """A script's API token downloaded this document (#518): it's left out of that token's not_downloaded list."""
    if p.token is not None and db.get(ApiTokenDownload, (p.token.id, job.id)) is None:
        db.add(ApiTokenDownload(token_id=p.token.id, job_id=job.id))
        db.commit()


def _record_access(db: Session, request: Request, p: Principal, job: Job, f: JobFile, reason: str | None) -> None:
    """Downloads by anyone but the owner are recorded."""
    if job.user_id != p.user.id:
        audit.record(db, request, "file.accessed", actor=p.user, target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"file": f.name, "kind": f.kind, "owner": job.user.email,
                                                     "reason": reason})
        db.commit()


@router.get("/jobs/{job_id}/files/{file_id}", dependencies=[Depends(per_person("download", "download"))])
def download(job_id: uuid.UUID, file_id: uuid.UUID, request: Request, reason: str | None = Query(None, max_length=500),
             p: Principal = Depends(document_principal("download")), db: Session = Depends(get_db)):
    job = _visible_job(db, job_id, p)
    f = _file(db, job, file_id)
    if f.kind in STAFF_ONLY_FILES and not p.is_super_admin:
        raise not_found("File")
    if p.token is not None and not api_tokens.may_download(p.token, f.kind):
        raise ApiError(403, "token_scope", "This API token can't download this file. Make one that can in Settings.")
    _mark_downloaded(db, p, job)
    _record_access(db, request, p, job, f, reason)
    disposition = f"attachment; filename=\"{safe_name(f.name)}\""
    return StreamingResponse(get_storage().get_stream(f.s3_key), media_type=f.content_type,
                             headers={"Content-Disposition": disposition, "Content-Length": str(f.size_bytes),
                                      "X-Content-Type-Options": "nosniff"})


@router.get("/jobs/{job_id}/report")
def report_markdown(job_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    """The Markdown report as text, for display in the web app."""
    job = _visible_job(db, job_id, p)
    f = next((f for f in job.files if f.kind == "report_md"), None)
    if f is None:
        raise not_found("Report")
    f = _file(db, job, f.id)
    _record_access(db, request, p, job, f, None)
    text = b"".join(get_storage().get_stream(f.s3_key)).decode("utf-8", "replace")
    return {"markdown": text}


# --- exporting many documents' files as one zip (#481) ---------------------------------------------

class ExportFilters(BaseModel):
    """The list's search, filters and sort, as /documents takes them: everything it shows, every page."""
    q: str = Field("", max_length=200)
    status: str | None = Field(None, max_length=20)
    result: str | None = Field(None, max_length=20)
    service: str | None = Field(None, max_length=64)
    audit: bool | None = None
    since: datetime | None = None
    until: datetime | None = None
    sort: str = Field("-created_at", max_length=20)


class ExportRequest(BaseModel):
    """The documents chosen (ids), or everything the list shows with its filters."""
    ids: list[uuid.UUID] | None = Field(None, max_length=1000)
    filters: ExportFilters | None = None


class LeftOut(BaseModel):
    filename: str
    reason: str


class ExportPlanOut(BaseModel):
    """What the zip will hold, shown before downloading. matched: the documents asked for; past `limit` (the
    max_export_documents setting) the export is refused."""
    matched: int
    limit: int
    documents: int
    files: int
    size_bytes: int
    left_out: list[LeftOut]


def _export_jobs(db: Session, scope: list, body: ExportRequest, *, people: bool, limit: int) -> tuple[list[Job], int]:
    """The documents asked for, in scope and not deleted (at most limit + 1, to know the export is too big), and
    how many were asked for."""
    if body.ids is not None:
        ids = list(dict.fromkeys(body.ids))
        jobs = db.scalars(select(Job).where(*scope, Job.deleted_at.is_(None), Job.id.in_(ids))
                          .order_by(Job.created_at.desc(), Job.id).limit(limit + 1)).all()
        return list(jobs), len(jobs) if len(ids) <= limit else len(ids)
    if body.filters is None:
        raise ApiError(422, "bad_export", "Choose documents, or export everything the list shows.")
    f = body.filters
    where, order = documents_query(scope, q=f.q, status=f.status, result=f.result, service=f.service, audit=f.audit,
                                   since=f.since, until=f.until, sort=f.sort, people=people)
    matched = db.scalar(select(func.count()).select_from(Job).where(*where)) or 0
    return list(db.scalars(select(Job).where(*where).order_by(*order).limit(limit + 1))), matched


def export_plan(db: Session, p: Principal, scope: list, body: ExportRequest, *, people: bool = False
                ) -> tuple[export.Plan, int, int]:
    runtime = settings_store.load(db)
    limit = runtime.max_export_documents
    jobs, matched = _export_jobs(db, scope, body, people=people, limit=limit)
    plan = export.plan(db, jobs[:limit], is_super_admin=p.is_super_admin, time_zone=runtime.report_time_zone,
                       retention_days=runtime.file_retention_days)
    return plan, matched, limit


def export_plan_out(plan: export.Plan, matched: int, limit: int) -> ExportPlanOut:
    return ExportPlanOut(matched=matched, limit=limit, documents=len(plan.documents), files=plan.files,
                         size_bytes=plan.size, left_out=[LeftOut(filename=j.filename, reason=why) for j, why in plan.left_out])


def export_zip(db: Session, request: Request, p: Principal, scope: list, body: ExportRequest, *, people: bool = False,
               group_id: uuid.UUID | None = None, where: str | None = None, reason: str | None = None,
               name: str | None = None) -> StreamingResponse:
    """The zip, streamed. Audited before the first byte goes: the export (documents.exported), and each file from
    someone else's document as a download is (file.accessed, with its owner), so the bulk-download alert counts
    the files in an export."""
    plan, matched, limit = export_plan(db, p, scope, body, people=people)
    if matched > limit:
        raise ApiError(422, "too_many_documents", f"An export holds at most {limit} documents, and this would be "
                                                  f"{matched}. Choose fewer, or narrow the list.")
    if not plan.documents:
        raise ApiError(422, "nothing_to_export", "None of these documents has files to download.")
    others = 0
    for doc in plan.documents:
        if doc.job.user_id == p.user.id:
            continue
        for e in doc.entries:
            others += 1
            audit.record(db, request, "file.accessed", actor=p.user, target_type="job", target_id=doc.job.id,
                         group_id=doc.job.group_id, details={"file": e.file.name, "kind": e.file.kind,
                                                             "owner": doc.job.user.email, "reason": reason, "via": "export"})
    audit.record(db, request, "documents.exported", actor=p.user, target_type="group" if group_id else None,
                 target_id=group_id, group_id=group_id or p.user.group_id,
                 details={"documents": len(plan.documents), "files": plan.files, "size_bytes": plan.size,
                          "left_out": len(plan.left_out), "others_files": others,
                          "list": where or ("group documents" if group_id else "my documents"),
                          "how": "chosen" if body.ids is not None else "everything shown"})
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = name or f"documents-{stamp}.zip"
    return StreamingResponse(export.stream(plan, get_storage(), by=p.user.email), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                      "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/documents/export/plan", response_model=ExportPlanOut)
def my_export_plan(body: ExportRequest, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What exporting these of the person's documents would hold: shown before downloading."""
    return export_plan_out(*export_plan(db, p, [Job.user_id == p.user.id], body))


@router.post("/documents/export", dependencies=[Depends(per_person("document_export"))])
def my_export(body: ExportRequest, request: Request, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    """The person's chosen documents (or everything their list shows), every file they may download, as one zip
    made on the fly. One request against its own hourly limit, however many files."""
    return export_zip(db, request, p, [Job.user_id == p.user.id], body)


@router.get("/jobs/{job_id}/files.zip", dependencies=[Depends(per_person("document_export", "download"))])
def document_zip(job_id: uuid.UUID, request: Request, reason: str | None = Query(None, max_length=500),
                 p: Principal = Depends(document_principal("download")), db: Session = Depends(get_db)):
    """Every file the document's page offers this person, as one zip (#507), as the list's zip export makes it: named
    from its date and file name with underscores ("2026-10-21_syllabus.zip"). Someone else's files are recorded as
    downloads are, with the reason given."""
    job = _visible_job(db, job_id, p)
    if job.files_deleted_at is not None:
        raise ApiError(410, "files_deleted", "This document's files have been removed.")
    if p.token is not None and p.token.download != "all":
        raise ApiError(403, "token_scope", "This API token downloads chosen files, not the zip. Make one that can in Settings.")
    _mark_downloaded(db, p, job)
    plan, _, _ = export_plan(db, p, [Job.id == job.id], ExportRequest(ids=[job.id]))
    name = safe_name(plan.documents[0].folder.replace(" ", "_") + ".zip", "document.zip") if plan.documents else None
    return export_zip(db, request, p, [Job.id == job.id], ExportRequest(ids=[job.id]), where="document page",
                      reason=reason, name=name)
