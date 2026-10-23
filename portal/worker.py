"""Background worker: sends queued jobs to the engine, follows them, collects results, settles
pages, emails users, and runs the retention and records purges.

Run with `python -m portal.worker`. Several copies can run at once: jobs are claimed with
SELECT ... FOR UPDATE SKIP LOCKED.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from portal import (
    alerts, api_tokens, audit, email_reports, helpdesk, metrics, notifications, outbox, pool_alerts, prometheus, queues, quota,
    removal, report_views, security_alerts, services, settings_store, siem,
)
from portal.config import get_portal_settings
from portal.db import system_session
from portal.engine import AIChoice, EngineError, EngineUnavailable, get_engine_client
from portal.mail import Mail
from portal.models import (
    ACTIVE_JOB_STATUSES, AiKind, AuditEvent, AuthSession, DocumentVersion, Group, Job, JobFile, OtpChallenge,
    QuotaLedger, Service,
)
from portal.storage import get_storage, job_prefix, safe_name

log = logging.getLogger("portal.worker")

JOB_TIMEOUT = timedelta(hours=12)

FRIENDLY_ERRORS = {
    "result_not_saved": "The document was processed, but its result couldn't be saved. "
                        "The problem has been logged for the support team.",
    "ai_provider_unavailable": "The AI service wasn't available, so the document couldn't be processed. "
                               "Try again later.",
    "tag_integrity_failed": "The remediation engine couldn't produce a valid file for this document. "
                            "The problem has been logged for the support team.",
    "time_limit_exceeded": "This document took too long to process.",
    "worker_lost": "The remediation engine stopped unexpectedly while processing this document (it may be too large "
                   "for the engine's memory). Your pages have been given back.",
    "stopped": "Processing of this document was stopped.",
    "engine_rejected": "The remediation engine refused this document.",
    "engine_lost": "The remediation engine lost track of this document. Try submitting it again.",
    "adobe_quota": "Adobe's usage limit for this service has been reached, so the document wasn't tagged. "
                   "Your pages have been given back. Try again later or use the standard option.",
    "adobe_rejected": "Adobe couldn't read this PDF (it may be damaged or password protected).",
    "adobe_failed": "Adobe didn't finish tagging this document. Your pages have been given back; try again later.",
    "adobe_unavailable": "Adobe Autotag isn't available at the moment.",
}

CONTENT_TYPES = {".pdf": "application/pdf", ".xml": "application/xml", ".md": "text/markdown; charset=utf-8",
                 ".json": "application/json", ".zip": "application/zip",
                 ".mxl": "application/vnd.recordare.musicxml", ".musicxml": "application/vnd.recordare.musicxml+xml",
                 # The formula list (#403): always downloaded (attachment), never shown on the portal's own site
                 ".html": "text/html; charset=utf-8",
                 ".brf": "application/x-brf"}  # braille ready format (#403)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- result summary ------------------------------------------------------------------------------


def _limits_hit(metrics: dict, findings: int) -> list[dict]:
    """The limits the document reached (#312): the engine's, and the review list this portal keeps being cut."""
    out = [x for x in (metrics.get("limits_hit") or []) if isinstance(x, dict)]
    kept = get_portal_settings().review_findings_max
    if findings > kept:
        out.append({"setting": "PORTAL_REVIEW_FINDINGS_MAX", "value": kept, "wanted": findings,
                    "effect": "review items past it aren't in the Needs review list (the JSON report has them all)"})
    return out

def improved_label(resp: dict) -> str:
    """Name of the Adobe route's improved copy, from what was done to it."""
    imp = (resp.get("reports") or {}).get("improvements") or {}
    alt = bool(imp.get("alt_text") or imp.get("decorative"))
    fixes = any(imp.get(k) for k in ("glyph_lists", "unicode_maps", "glyph_widths", "bookmarks", "title",
                                     "pdfua_identifier", "header_scope"))
    if alt and fixes:
        return "Adobe + AI alt text and fixes"
    return "Adobe + fixes" if fixes else "Adobe + AI alt text"


def summarize(resp: dict) -> dict:
    """The parts of the engine's response the web app shows, small enough to keep in Postgres."""
    reports = resp.get("reports") or {}
    comp = reports.get("comparison") or {}
    before, after = comp.get("before") or {}, comp.get("after") or {}
    files = resp.get("files") or {}
    verifications = reports.get("tag_verification") or {}
    tv = verifications.get("primary") or {}
    # The Adobe route's improved copy (Adobe's file with AI alt text and fixes) is the document people get (#499): the
    # verdict, the tiles and Needs review are about it. Adobe's file as it came back is kept as a choice.
    adobe_after = None
    verdict_of = "primary"
    if files.get("enhanced") and comp.get("after_enhanced"):
        adobe_after, after = after, comp["after_enhanced"]
        tv = verifications.get("enhanced") or tv
        verdict_of = "enhanced"
    findings = tv.get("findings") or []
    severity_rank = {"high": 0, "review": 1, "suggest": 2}
    findings = sorted(findings, key=lambda f: (severity_rank.get(f.get("severity"), 3), f.get("page") or 0))
    metrics = resp.get("metrics") or {}
    visual = comp.get("visual_fidelity") or {}
    # Which PDF/UA the verdicts are by: 2 for a PDF 2.0 document, else 1 (#409)
    part = 2 if (after or before).get("pdfua_part") == 2 else 1
    return {
        "pdfua_part": part,
        "compliant_before": before.get("compliant"),
        # The document came tagged and its tags were kept (the tag manager then shows which tags were its own)
        "tags_kept": bool((comp.get("existing_tags") or {}).get("present")
                          and not (comp.get("existing_tags") or {}).get("rebuilt_tree")),
        "compliant_after": after.get("compliant"),
        "verdict_of": verdict_of,  # the version the verdict, tiles and review items describe
        "adobe_file_compliant": adobe_after.get("compliant") if adobe_after is not None else None,
        "failed_checks_before": before.get("failed_checks"),
        "failed_checks_after": after.get("failed_checks"),
        "failed_rules_after": after.get("failed_rules"),
        "visual_file_compliant": (comp.get("after_visual_fallback") or {}).get("compliant"),
        "two_files": bool(files.get("remediated_visual_fallback")),
        "destructive_pages": comp.get("destructive_pages") or [],
        "fonts_embedded": comp.get("fonts_embedded") or [],
        "visual_fidelity": visual if isinstance(visual, dict) else {},
        "remaining_failures": [
            {k: f.get(k) for k in ("clause", "description", "failed_checks", "pages")}
            for f in ((after.get("verapdf") or {}).get(f"ua{part}") or {}).get("failures", [])][:50],
        "before_after": {k: [before.get(k), after.get(k)] for k in (
            "headings", "figures", "figures_missing_alt", "tables", "tables_without_headers",
            "content_tagged_ratio", "language", "title")},
        "review": {
            "integrity_passed": (tv.get("integrity") or {}).get("passed"),
            "summary": tv.get("summary") or {},
            "ai": {k: (tv.get("ai") or {}).get(k) for k in ("mode", "reviewed", "results", "judges")},
            "findings": findings[:get_portal_settings().review_findings_max],
            "findings_total": len(findings),
        },
        "summary": reports.get("remediation_summary") or "",
        "warnings": resp.get("warnings") or [],
        "metrics": {k: metrics.get(k) for k in ("pages_processed", "processing_time_seconds", "ai_calls",
                                                 "ai_provider", "ai_model", "images_described", "ocr_pages", "route",
                                                 "stage_seconds", "notation_read_by_ai", "notation_ai_rejected",
                                                 "notation_ai_rejected_by_reason", "notation_by_rule", "ai_failures",
                                                 "tables_analyzed", "reading_order_pages_ai", "music_examples",
                                                 "music_read", "scan_figures", "ocr_ai_words_corrected",
                                                 "ocr_ai_lines_transcribed", "ocr_ai_rejected")}
        | {"limits_hit": _limits_hit(metrics, len(findings))},
        "adobe": {k: (metrics.get("adobe") or {}).get(k) for k in ("parts", "pages_sent", "seconds")}
        if metrics.get("adobe") else None,
        "improved_label": improved_label(resp) if files.get("enhanced") else None,
        "improvements": reports.get("improvements"),
    }


# The parts of a summary that describe one file: what changed when the Adobe route's improved copy became the
# document people get (#499)
VERDICT_KEYS = ("pdfua_part", "compliant_after", "verdict_of", "adobe_file_compliant", "failed_checks_after",
                "failed_rules_after", "remaining_failures", "before_after", "review")


def resummarize_improved(db: Session, *, dry_run: bool = False) -> tuple[int, int]:
    """Documents finished before #499 whose verdict was Adobe's file though they have an improved copy: their verdict,
    tiles and review items recomputed from the engine's JSON report (kept with their files), the rest of their
    summary as it was. Returns (updated, skipped: files removed or no report)."""
    import json
    updated = skipped = 0
    for job in db.scalars(select(Job).where(Job.status == "completed", Job.files_deleted_at.is_(None))):
        summary = job.result_summary or {}
        kinds = {f.kind: f for f in job.files}
        if summary.get("verdict_of") or "enhanced" not in kinds:
            continue
        report = kinds.get("report_json")
        try:
            resp = json.loads(b"".join(get_storage().get_stream(report.s3_key))) if report else None
        except Exception:  # noqa: BLE001 - gone from the store: leave it as it is
            resp = None
        if not resp:
            skipped += 1
            continue
        new = summarize(resp)
        if not dry_run:
            job.result_summary = {**summary, **{k: new.get(k) for k in VERDICT_KEYS}}
        updated += 1
    if not dry_run:
        db.commit()
    return updated, skipped


def _engine_files(resp: dict) -> list[tuple[str, str]]:
    """(kind, engine path) for every file worth keeping."""
    files = resp.get("files") or {}
    reports = resp.get("reports") or {}
    after = reports.get("verapdf_after") or {}
    pairs = [
        ("primary", files.get("remediated_primary")),
        ("visual_fallback", files.get("remediated_visual_fallback")),
        ("report_md", reports.get("markdown_report")),
        ("report_json", reports.get("full_report")),
        ("verapdf_before", reports.get("verapdf_before")),
        ("verapdf_after", after.get("primary")),
        ("verapdf_after_visual", after.get("visual_fallback")),
    ]
    maps = reports.get("tag_maps") or {}
    pairs += [("enhanced", files.get("enhanced")), ("verapdf_after_enhanced", after.get("enhanced"))]
    pairs += [(f"tagmap_{label}", maps.get(label)) for label in ("original", "primary", "visual_fallback", "enhanced")]
    pairs += [("adobe_report", p) for p in reports.get("adobe_reports") or []]
    pairs.append(("report_pdf", reports.get("report_pdf")))
    pairs.append(("music_files", reports.get("music_files")))
    pairs.append(("music_score", reports.get("music_score")))
    pairs.append(("math_files", reports.get("math_files")))
    pairs.append(("math_list", reports.get("math_list")))
    pairs.append(("math_nemeth", reports.get("math_nemeth")))
    pairs.append(("math_ueb", reports.get("math_ueb")))
    return [(k, p) for k, p in pairs if isinstance(p, str) and p.startswith("/api/v1/jobs/")]


# --- mail ----------------------------------------------------------------------------------------

def _job_mail(job: Job) -> Mail:
    s = get_portal_settings()
    link = f"{s.app_url.rstrip('/')}/jobs/{job.id}"
    if job.status == "completed":
        r = job.result_summary or {}
        verdict = "passes" if r.get("compliant_after") else "doesn't fully pass yet"
        review = (r.get("review") or {}).get("findings_total") or 0
        body = (f"Your document \"{job.filename}\" has been processed.\n\n"
                f"Result: it {verdict} the PDF/UA-{r.get('pdfua_part') or 1} accessibility check.\n"
                f"Items for a person to review: {review}\n\n"
                f"See the results and download the files:\n{link}\n")
        subject = f"Done: {job.filename}"
    else:
        body = (f"Your document \"{job.filename}\" couldn't be processed.\n\n{job.error_message}\n\n"
                f"No pages were charged.\n\nDetails: {link}\n")
        subject = f"Failed: {job.filename}"
    return Mail(to=job.user.email, subject=subject, text=body)


# --- steps ---------------------------------------------------------------------------------------

def _no_nul(x):
    """The engine's answer without NUL characters, which Postgres can't keep in text or JSONB (alt text read from a
    PDF can carry one); one of them made saving a finished document fail on every pass."""
    if isinstance(x, str):
        return x.replace("\x00", "")
    if isinstance(x, dict):
        return {_no_nul(k): _no_nul(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_no_nul(v) for v in x]
    return x


# Liveness (#335): touched after every pass that completes. A pass that fails is logged and the loop goes on, so a
# worker failing every pass (as on 16 October, until #324) looked alive; with no completed pass for the probe's
# window, the pod is restarted, and its restarts show the trouble.
ALIVE_FILE = Path(os.environ.get("PORTAL_WORKER_ALIVE_FILE", "/tmp/portal-worker.alive"))


def touch_alive(path: Path | None = None) -> None:
    try:
        (path or ALIVE_FILE).touch()
    except OSError:
        pass


def _fail(db: Session, job: Job, code: str, detail: str | None) -> None:
    log.warning("job %s failed: %s %s", job.id, code, detail)
    quota.release(db, job, code)
    job.status = "failed"
    job.error_code = code
    job.error_message = FRIENDLY_ERRORS.get(code, "Remediation failed for this document.")
    job.progress = None
    job.finished_at = _now()
    # The engine's detail (paths, internal messages) goes to the super-admin note of the help request below and
    # the log, not to the audit row, which group admins read
    audit.record(db, None, "job.failed", actor_email="system", target_type="job", target_id=job.id,
                 group_id=job.group_id, outcome="failure", details={"code": code})
    try:
        with db.begin_nested():  # a problem opening the request must not undo the failure itself
            helpdesk.open_for_failure(db, job, code, detail)
    except Exception:  # noqa: BLE001
        log.exception("job %s: no help request could be opened for the failure", job.id)


def is_large(job: Job, threshold: int) -> bool:
    return threshold > 0 and job.pages > threshold


def _smaller_since_last_large(db: Session, threshold: int) -> int | None:
    """How many smaller documents were started since the last large one (None: no large one yet)."""
    last = db.scalar(select(func.max(Job.submitted_at)).where(Job.pages > threshold, Job.submitted_at.is_not(None)))
    if last is None:
        return None
    return db.scalar(select(func.count()).select_from(Job).where(Job.pages <= threshold, Job.submitted_at > last)) or 0


def _pick_queued(db: Session, slots: int, rs: settings_store.RuntimeSettings, in_use: int = 0,
                 places: int = 0) -> list[Job]:
    """Which waiting documents to start now, oldest first, within these limits:

    - nobody gets more than `max_concurrent_jobs_per_user` at the engine at once, so one batch doesn't hold up
      everyone else;
    - paused services and daily page caps hold their documents back;
    - a service's "at once" limit (a local AI's GPUs can only serve so many) holds the rest back;
    - large documents (more than `large_doc_pages`) are mixed in: one after every `large_doc_every` smaller ones,
      no more than `large_doc_at_once` running, and one after another when nothing smaller is waiting. A super
      admin's "process now" skips the large-document rules.

    in_use, places: the engine's places in use and in all, for the note on what's left waiting for one (#530)."""
    per_user, threshold = rs.max_concurrent_jobs_per_user, rs.large_doc_pages
    active = (Job.status.in_(("submitted", "processing")))
    running = dict(db.execute(select(Job.user_id, func.count()).where(active).group_by(Job.user_id)).all())
    candidates = db.scalars(select(Job).where(Job.status == "queued")
                            .order_by(Job.expedited_at.is_(None), Job.expedited_at, Job.created_at)
                            .limit(max(slots * 20, 200)).with_for_update(skip_locked=True)).all()
    caps = {s.key: s.daily_cap_pages for s in db.scalars(select(Service).where(Service.daily_cap_pages.is_not(None)))}
    pauses = queues.paused(db)
    sent_today = {k: quota.pages_sent_today(db, k) for k in caps}
    # Services with a limit on documents at once, and how many each has at the engine now
    at_once = {s.key: s.max_at_once for s in db.scalars(select(Service).where(Service.max_at_once.is_not(None)))}
    using: dict[str, int] = {k: 0 for k in at_once}
    if at_once:
        for j in db.scalars(select(Job).where(active)):
            for k in queues.job_services(j):
                if k in using:
                    using[k] += 1
    full = lambda job: [k for k in queues.job_services(job) if k in at_once and using[k] >= at_once[k]]  # noqa: E731

    def note(job: Job, value: dict | None) -> None:
        if job.progress != value:
            job.progress = value

    # What could start, before the large-document rules
    ready: list[Job] = []
    for job in candidates:
        held = queues.paused_services(job, pauses)
        if held:  # a paused service holds its documents in the queue until it's resumed
            note(job, {"stage": "paused", "services": held})
            continue
        # A service's daily page cap holds the job back until tomorrow (or until the cap is raised)
        over = [k for k in job.charged_services or [] if k in caps and sent_today[k] + job.pages_charged > caps[k]]
        if over:
            note(job, {"stage": "waiting_daily_cap", "services": over})
            continue
        ready.append(job)

    large_running = db.scalar(select(func.count()).select_from(Job).where(active, Job.pages > threshold)) \
        if threshold > 0 else 0
    since = _smaller_since_last_large(db, threshold) if threshold > 0 else None
    smaller_done = rs.large_doc_every if since is None else since

    def can_start(job: Job) -> bool:
        if running.get(job.user_id, 0) >= per_user:
            return False
        if any(k in sent_today and sent_today[k] + job.pages_charged > caps[k] for k in job.charged_services or []):
            return False
        return not full(job)

    picked: list[Job] = []
    small = [j for j in ready if not is_large(j, threshold) or j.expedited_at]
    large = [j for j in ready if is_large(j, threshold) and not j.expedited_at]
    while len(picked) < slots:
        small_next = next((j for j in small if can_start(j)), None)
        large_next = next((j for j in large if can_start(j)), None)
        large_turn = large_next is not None and large_running < rs.large_doc_at_once and (
            small_next is None or smaller_done >= rs.large_doc_every)
        if large_turn:
            job = large_next
            large.remove(job)
            large_running += 1
            smaller_done = 0
        elif small_next is not None:
            job = small_next
            small.remove(job)
            if is_large(job, threshold):  # a large document someone chose to process now
                large_running += 1
            else:
                smaller_done += 1
        else:
            break
        for k in job.charged_services or []:
            if k in sent_today:
                sent_today[k] += job.pages_charged
        for k in queues.job_services(job):
            if k in using:
                using[k] += 1
        running[job.user_id] = running.get(job.user_id, 0) + 1
        picked.append(job)
    # Say why what's left is waiting: large documents wait their turn; the rest wait for the person's own documents
    # at the engine, or for a free place there (#530: shown to super admins, with the numbers)
    for job in small + large:
        over = [k for k in job.charged_services or [] if k in caps and sent_today[k] + job.pages_charged > caps[k]]
        if over:
            note(job, {"stage": "waiting_daily_cap", "services": over})
        elif full(job):
            note(job, {"stage": "waiting_at_once", "services": full(job)})
        elif job in large:
            note(job, {"stage": "waiting_large", "every": rs.large_doc_every})
        elif running.get(job.user_id, 0) >= per_user:
            note(job, {"stage": "waiting_per_person", "at_once": per_user})
        else:
            note(job, _no_place(in_use + len(picked), places))
    return picked


def _no_place(in_use: int, places: int) -> dict:
    """The note on a document waiting for a free place at the engine (#530)."""
    return {"stage": "waiting_engine", "in_use": in_use, "places": places}


def _note_no_place(db: Session, in_use: int, places: int) -> None:
    """Every place at the engine is in use: the documents waiting only for one say so, with the numbers (#530).
    Notes from a pause or a limit are left as they are: those still hold their documents back."""
    for job in db.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(500)
                          .with_for_update(skip_locked=True)):
        if (job.progress or {}).get("stage") in (None, "", "waiting_engine", "waiting_per_person"):
            value = _no_place(in_use, places)
            if job.progress != value:
                job.progress = value


def _scanner() -> dict | None:
    """The malware scanner as it answers now, for its alerts (#455); None with scanning off."""
    from portal import malware
    if not malware.enabled():
        return None
    try:
        version, published = malware.signatures()
    except malware.ScannerDown as e:
        return {"error": str(e)}
    return {"version": version, "signatures": published}


_capacity: tuple[float, int | None] = (0.0, None)


def in_flight_limit() -> int:
    """How many jobs to keep at the engine: the configured number, or the engine's running capacity when that's
    more (asked every half minute, as workers come and go with the queue)."""
    global _capacity
    s = get_portal_settings()
    now = time.monotonic()
    if now - _capacity[0] >= s.engine_capacity_cache_seconds:
        ask = getattr(get_engine_client(), "capacity", None)
        _capacity = (now, ask() if ask else None)
    return min(s.engine_in_flight_ceiling, max(s.engine_max_in_flight, _capacity[1] or 0))


def submit_queued(db: Session) -> int:
    places, in_use = in_flight_limit(), _engine_in_flight(db)
    slots = places - in_use
    if slots <= 0:
        _note_no_place(db, in_use, places)
        db.commit()
        return 0
    rs = settings_store.load(db)
    jobs = _pick_queued(db, slots, rs, in_use, places)
    engine, storage, sent = get_engine_client(), get_storage(), 0
    for job in jobs:
        src = next((f for f in job.files if f.kind == "input"), None)
        if src is None:
            _fail(db, job, "engine_rejected", "input file missing")
            db.commit()
            continue
        try:
            with tempfile.TemporaryFile() as tmp:
                storage.get_to_file(src.s3_key, tmp)
                if job.ai_kind_auto and job.ai_kind_pick is None:
                    tmp.seek(0)
                    _classify(db, engine, job, tmp)
                tmp.seek(0)
                job.engine_job_id = engine.submit(safe_name(job.filename), tmp, job.report_only, _ai_choice(db, job),
                                                  full_review=job.full_review, route=_route(db, job),
                                                  branding=_branding(db, job), no_ai=job.no_ai,
                                                  validator=validator_flags(rs),
                                                  ignore_existing=job.ignore_existing)
        except EngineUnavailable as e:
            job.submit_attempts += 1
            log.warning("engine unavailable, job %s stays queued: %s", job.id, e)
            db.commit()
            break
        except EngineError as e:
            _fail(db, job, "engine_rejected", str(e))
            db.commit()
            continue
        job.status = "submitted"
        job.submitted_at = _now()
        db.commit()
        sent += 1
    db.commit()
    return sent


def poll_active(db: Session) -> int:
    jobs = db.scalars(select(Job).where(Job.status.in_(("submitted", "processing"))).order_by(Job.submitted_at)
                      .with_for_update(skip_locked=True)).all()
    engine, done = get_engine_client(), 0
    for job in jobs:
        try:
            resp = _no_nul(engine.status(job.engine_job_id))
        except EngineUnavailable as e:
            log.warning("engine unavailable while polling %s: %s", job.id, e)
            db.commit()
            break
        except EngineError as e:
            _fail(db, job, "engine_lost", str(e))
            _notify(db, job)
            continue
        status = resp.get("status")
        if status == "processing":
            if job.submitted_at and _now() - job.submitted_at > JOB_TIMEOUT:
                _fail(db, job, "time_limit_exceeded", "no result from the engine in time")
                _notify(db, job)
                continue
            job.status = "processing"
            job.progress = resp.get("progress")
            db.commit()
        elif status == "failed":
            err = resp.get("error") or {}
            if err.get("error") == "ai_provider_unavailable" and job.ai_kind and not job.ai_kind_fallback:
                # The kind's model failed: try again with the level's General models (pages stay reserved)
                job.ai_kind_fallback = "failed"
                job.status, job.engine_job_id, job.submitted_at, job.progress = "queued", None, None, None
                audit.record(db, None, "job.ai_fallback", actor_email="system", target_type="job", target_id=job.id,
                             group_id=job.group_id, details={"level": job.ai_tier, "kind": job.ai_kind,
                                                             "detail": (err.get("detail") or "")[:500]})
                db.commit()
                continue
            _fail(db, job, err.get("error") or "failed", err.get("detail"))
            _notify(db, job)
            done += 1
        elif status == "completed":
            try:
                _collect(db, job, resp)
            except EngineUnavailable as e:
                db.rollback()
                log.warning("couldn't fetch results for %s yet: %s", job.id, e)
                continue
            except Exception as e:  # noqa: BLE001 - one document's result mustn't stop every other document
                db.rollback()
                log.exception("couldn't save the result of %s", job.id)
                _fail(db, job, "result_not_saved", f"{type(e).__name__}: {e}"[:2000])
                db.commit()
                _notify(db, job)
                continue
            _notify(db, job)
            done += 1
    db.commit()
    return done


def _route(db: Session, job: Job) -> str:
    """The engine route of the job's processing service. Reports are always made by the standard route."""
    s = db.get(Service, job.processing)
    return "standard" if job.report_only or s is None else s.engine_route


_models_seen: tuple[float, set[str] | None] = (0.0, None)


def _gateway_models() -> set[str] | None:
    """The models the AI gateway serves, checked at most once a minute. None: couldn't tell (assume available)."""
    global _models_seen
    at, models = _models_seen
    if time.monotonic() - at > 60:
        try:
            models = set(get_engine_client().ai_models())
        except (EngineUnavailable, EngineError, AttributeError):
            models = None
        _models_seen = (time.monotonic(), models)
    return models


def _classify(db: Session, engine, job: Job, fileobj) -> None:
    """"Let AI decide": pick the kind from the document's first pages before it's sent. Below the threshold, or if
    the classifier can't be used, General is used and the job says why. Never holds the document up."""
    rs = settings_store.load(db)
    group = db.get(Group, job.group_id) if job.group_id else None
    kinds = services.kinds(db, job.ai_tier) if job.ai_tier else []
    choice = services.classifier_for(db, group)
    pick: dict = {"kind": None, "label": "General", "confidence": None, "used": False}
    if not kinds or choice is None:
        job.ai_kind_pick = {**pick, "reason": "unavailable"}
        return
    backend, model = choice
    offered = [{"key": k.key, "label": k.label, "description": k.description} for k in kinds]
    try:
        res = engine.classify(safe_name(job.filename), fileobj, offered, backend, model, rs.classifier_pages)
    except (EngineUnavailable, EngineError) as e:
        log.warning("classifier failed for job %s: %s", job.id, e)
        job.ai_kind_pick = {**pick, "by": backend, "reason": "failed"}
        return
    if backend == "jev":
        job.external_services = [*(job.external_services or []), "classifier"]
    by_key = {k.key: k for k in kinds}
    kind, confidence = res.get("kind"), res.get("confidence")
    pick.update(by=backend, confidence=confidence)
    if kind in by_key:
        pick.update(kind=kind, label=by_key[kind].label)
        if confidence is not None and confidence >= rs.classifier_threshold:
            pick["used"] = True
            job.ai_kind = kind
        else:
            pick["reason"] = "unsure"
    elif kind == services.GENERAL:
        pick["used"] = True
    else:
        pick["reason"] = res.get("reason") or "no_answer"  # no_text: a scan with no text yet
    job.ai_kind_pick = pick
    cost = res.get("cost_usd")
    if cost:
        job.cost = {**(job.cost or {}), "classifier_usd": cost}


def _kind(db: Session, job: Job) -> AiKind | None:
    """The job's kind of document, if it's still to be used (not General, and no fallback yet)."""
    if not job.ai_tier or not job.ai_kind or job.ai_kind_fallback:
        return None
    return db.get(AiKind, (job.ai_tier, job.ai_kind))


def _ai_choice(db: Session, job: Job) -> AIChoice | None:
    """The job's AI level, as the gateway models the engine should use: its kind of document's, or the level's
    General ones. If the kind's model isn't available, General is used and the job says so. None: the engine's
    default AI."""
    if not job.ai_tier:
        return None
    s = db.get(Service, job.ai_tier)
    if s is None or not s.gateway_model:
        return None
    model, judge = s.gateway_model, s.gateway_judge_model
    if job.ai_kind and not job.ai_kind_fallback:
        k = _kind(db, job)
        models = _gateway_models()
        if k is None or not k.enabled or (models is not None and k.gateway_model not in models):
            job.ai_kind_fallback = "unavailable"
            log.warning("job %s: kind %s of %s isn't available; using General", job.id, job.ai_kind, job.ai_tier)
        else:
            model, judge = k.gateway_model, k.gateway_judge_model or s.gateway_judge_model
    return AIChoice(job.ai_tier, model, judge, notation=s.allow_notation, ocr=s.allow_ocr_ai)


def validator_flags(rs) -> dict:
    """How the engine runs veraPDF for a job (the super admins' choice)."""
    return {"validator": rs.verapdf_mode, "verapdf_ua2": rs.verapdf_ua2}


def _branding(db: Session, job: Job) -> dict:
    """What the engine's PDF report shows: the service, the organization, the accent color and the AI level by
    name (never a model)."""
    rs = settings_store.load(db)
    from portal.routers.jobs import _ai_label
    out = {"service_name": rs.service_name, "organization": rs.organization_name or "",
           "accent_color": rs.accent_color or "", "ai_label": _ai_label(job) or ""}
    return {k: v for k, v in out.items() if v}


def _judge_for(db: Session, job: Job) -> str | None:
    """The review model a document was processed with (its kind's, else its level's), for saves and re-reviews."""
    s = db.get(Service, job.ai_tier) if job.ai_tier else None
    if s is None:
        return None
    k = _kind(db, job)
    return (k.gateway_judge_model if k is not None and k.enabled and k.gateway_judge_model else None) \
        or s.gateway_judge_model


def _ai_use(db: Session, job: Job, m: dict) -> dict:
    """What the AI did for a document, from the engine's metrics, for the AI usage report: tokens and cost for the
    writing model and the review, failed calls, the models that served them, and how long the job took."""
    if not job.ai_tier and not int(m.get("ai_calls") or 0) and not int(m.get("ai_failures") or 0):
        # No AI was used (no AI level, and the engine's own default AI wasn't either)
        return {"seconds": m.get("processing_time_seconds")} if m.get("processing_time_seconds") is not None else {}
    tok = m.get("ai_tokens_used") or {}
    rtok = m.get("verification_ai_tokens") or {}
    reviewed = bool(rtok.get("prompt_tokens") or rtok.get("completion_tokens") or m.get("verification_ai_cost_usd"))
    return {
        "writer_usd": round(float(m.get("ai_cost_usd") or 0), 6),
        "review_usd": round(float(m.get("verification_ai_cost_usd") or 0), 6),
        "prompt_tokens": int(tok.get("prompt_tokens") or 0),
        "completion_tokens": int(tok.get("completion_tokens") or 0),
        "review_prompt_tokens": int(rtok.get("prompt_tokens") or 0),
        "review_completion_tokens": int(rtok.get("completion_tokens") or 0),
        "ai_failures": int(m.get("ai_failures") or 0),
        "writer_model": m.get("ai_model") or job.ai_model,
        "review_model": (_judge_for(db, job) if job.ai_tier else None) if reviewed else None,
        "seconds": m.get("processing_time_seconds"),
    }


def _collect(db: Session, job: Job, resp: dict) -> None:
    engine, storage = get_engine_client(), get_storage()
    prefix = job_prefix(job.group_id, job.user_id, job.id)
    stored: dict[str, JobFile] = {f.kind: f for f in job.files if f.kind == "input"}
    for kind, path in _engine_files(resp):
        f = _store_engine_file(db, job, kind, path, path.rsplit("/", 1)[-1], prefix)
        stored.setdefault(kind, f)
    db.flush()
    _create_output_versions(db, job, resp, stored)
    job.result_summary = summarize(resp)
    m = resp.get("metrics") or {}
    job.cost = {"ai_usd": round(float(m.get("ai_cost_usd") or 0) + float(m.get("verification_ai_cost_usd") or 0), 6),
                "ai_calls": m.get("ai_calls") or 0, "ai_tier": job.ai_tier, **_ai_use(db, job, m),
                **({"classifier_usd": job.cost["classifier_usd"]} if (job.cost or {}).get("classifier_usd") else {})}
    if m.get("adobe"):
        a = m["adobe"]
        job.cost["adobe"] = {k: a.get(k) for k in ("pages_sent", "parts", "requests", "retries", "seconds")}
    # An AI tier that never worked for this document isn't charged: every AI call failed, so alt text,
    # tables and reading order fell back to layout rules
    all_failed = (int(m.get("ai_calls") or 0) == 0 and (int(m.get("ai_failures") or 0) > 0 or any(
        "every AI call failed" in w for w in resp.get("warnings") or [])))
    # On the Adobe route the AI only writes alt text: with no figures to describe it was never used
    unused = m.get("route") == "adobe" and int(m.get("ai_calls") or 0) == 0 and not all_failed
    if job.ai_tier and not job.report_only and (all_failed or unused) and job.ai_tier in (job.charged_services or []):
        quota.release_service(db, job, job.ai_tier, "every AI call failed" if all_failed else "AI not needed")
        job.cost = {**job.cost, "ai_refunded": True, **({"ai_unused": True} if unused else {})}
    job.status = "completed"
    job.progress = None
    job.finished_at = _now()
    if not job.report_only:
        quota.commit(db, job)
    audit.record(db, None, "job.completed", actor_email="system", target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"pages": job.pages_charged,
                                                 "compliant_after": job.result_summary.get("compliant_after")})


def _store_engine_file(db: Session, job: Job, kind: str, path: str, name: str, prefix: str) -> JobFile:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    folder = "output" if ext == ".pdf" else "reports"
    key = f"{prefix}{folder}/{safe_name(name)}"
    with tempfile.TemporaryFile() as tmp:
        size = get_engine_client().fetch(path, tmp)
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        get_storage().put_file(key, tmp, ctype)
    f = JobFile(job_id=job.id, kind=kind, name=name, s3_key=key, size_bytes=size, content_type=ctype)
    db.add(f)
    return f


def version_summary(resp: dict, label: str) -> dict:
    """Verdict and review findings for one file (original, primary, visual_fallback or reviewed)."""
    reports = resp.get("reports") or {}
    comp = reports.get("comparison") or {}
    snap = {"original": comp.get("before"), "visual_fallback": comp.get("after_visual_fallback"),
            "enhanced": comp.get("after_enhanced")}.get(
        label, comp.get("after")) or {}
    tv_label = {"reviewed": "primary"}.get(label, label)
    tv = (reports.get("tag_verification") or {}).get(tv_label) or {}
    findings = sorted(tv.get("findings") or [],
                      key=lambda f: ({"high": 0, "review": 1, "suggest": 2}.get(f.get("severity"), 3), f.get("page") or 0))
    out = {
        "pdfua_part": 2 if snap.get("pdfua_part") == 2 else 1,  # which PDF/UA the verdict is by (#409)
        "compliant": snap.get("compliant"),
        "failed_checks": snap.get("failed_checks"),
        "failed_rules": snap.get("failed_rules"),
        "findings": findings[:get_portal_settings().review_findings_max],
        "findings_total": len(findings),
        "review": {"integrity_passed": (tv.get("integrity") or {}).get("passed"), "summary": tv.get("summary") or {}},
    }
    if label == "reviewed":
        out["edits_applied"] = reports.get("edits_applied") or []
        out["visual_identical"] = (comp.get("visual_fidelity") or {}).get("identical")
        out["warnings"] = resp.get("warnings") or []
    return out


def _create_output_versions(db: Session, job: Job, resp: dict, stored: dict[str, JobFile]) -> None:
    def fid(kind):
        f = stored.get(kind)
        return f.id if f is not None else None

    specs = [("original", "Original", "input", "tagmap_original", "verapdf_before")]
    if not job.report_only:
        adobe = (resp.get("metrics") or {}).get("route") == "adobe"
        specs.append(("primary", "Adobe tagged" if adobe else "Remediated", "primary", "tagmap_primary",
                      "verapdf_after"))
        if "enhanced" in stored:
            specs.append(("enhanced", improved_label(resp), "enhanced", "tagmap_enhanced", "verapdf_after_enhanced"))
        if "visual_fallback" in stored:
            specs.append(("visual_fallback", "Visually exact", "visual_fallback", "tagmap_visual_fallback",
                          "verapdf_after_visual"))
    for number, (kind, label, pdf, tagmap, vera) in enumerate(specs):
        db.add(DocumentVersion(job_id=job.id, number=number, kind=kind, label=label, status="ready",
                               pdf_file_id=fid(pdf), tagmap_file_id=fid(tagmap), verapdf_file_id=fid(vera),
                               summary=version_summary(resp, kind), finished_at=_now()))


# --- human-reviewed versions ----------------------------------------------------------------------

VERSION_ERRORS = {
    "tag_integrity_failed": "These edits would have produced an invalid tag structure, so they weren't saved.",
    "engine_lost": "The remediation engine lost track of this save. Try saving again.",
}


def _engine_in_flight(db: Session) -> int:
    jobs = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("submitted", "processing"))))
    versions = db.scalar(select(func.count()).select_from(DocumentVersion).where(
        DocumentVersion.status.in_(("submitted", "processing"))))
    reviews = db.scalar(select(func.count()).select_from(DocumentVersion).where(
        DocumentVersion.status == "ready", (DocumentVersion.review_status == "running") | DocumentVersion.checking))
    return (jobs or 0) + (versions or 0) + (reviews or 0)


UNDO_HOURS = 24  # how long the file before a save into a reviewed version is kept (portal/routers/versions.py)


def _take_place(db: Session, v: DocumentVersion) -> None:
    """A version saved into another: once ready it takes that one's place, and the one before goes out of sight,
    kept a day so the save can be undone. If it failed, the one before simply stays, and this one says so."""
    if v.replaces_id is None:
        return
    before = db.get(DocumentVersion, v.replaces_id)
    if v.status == "ready":
        if before is not None and before.superseded_at is None:
            before.superseded_at = _now()
    elif not (v.label or "").endswith("(not saved)"):
        v.label = f"{v.label} (not saved)"[:100]


def purge_superseded(db: Session) -> int:
    """Remove versions a save replaced more than a day ago, with their files. Returns how many."""
    old = db.scalars(select(DocumentVersion).where(DocumentVersion.superseded_at.is_not(None),
                                                   DocumentVersion.superseded_at < _now() - timedelta(hours=UNDO_HOURS))
                     .with_for_update(skip_locked=True).limit(500)).all()
    for v in old:
        job = v.job
        get_storage().delete_prefix(job_prefix(job.group_id, job.user_id, job.id) + f"versions/{v.number}/")
        for fid in (v.pdf_file_id, v.tagmap_file_id, v.verapdf_file_id):
            f = db.get(JobFile, fid) if fid else None
            if f is not None:
                db.delete(f)
        db.delete(v)
    db.commit()
    return len(old)


def _fail_version(db: Session, v: DocumentVersion, code: str, detail: str | None) -> None:
    log.warning("version %s of job %s failed: %s %s", v.number, v.job_id, code, detail)
    if v.checking:
        # Opened early (#428): the file and its tags are good, only the checks after them didn't finish. It stays
        # open, and says so
        v.checking = False
        v.summary = {**(v.summary or {}), "checks_unfinished": True}
        v.error_code = code
        v.error_message = "The checks of this version didn't finish. The file and its tags were saved."
        v.finished_at = _now()
        return
    v.status = "failed"
    _take_place(db, v)
    v.error_code = code
    # The editor's own refusals are written for people; anything else gets a generic message.
    v.error_message = detail if code == "edit_rejected" and detail else VERSION_ERRORS.get(
        code, "Saving these edits failed. The previous version is unchanged.")
    v.finished_at = _now()
    if v.review_status in ("queued", "running"):
        v.review_status, v.review_error = "failed", "The version wasn't saved, so it wasn't reviewed."
    audit.record(db, None, "version.failed", actor_email="system", target_type="job", target_id=v.job_id,
                 group_id=v.job.group_id, outcome="failure", details={"version": v.number, "code": code})


def carried_ai_read(base: DocumentVersion | None) -> list[dict]:
    """The AI's readings flagged on the version an edit is based on, for the engine to flag again on the result
    (a reviewed version has no AI enrichment of its own): each finding's "ai_read", with its "readings" when two
    readings disagreed, and the element it was on."""
    out = []
    for f in ((base.summary or {}).get("findings") or []) if base else []:
        extra = f.get("extra") if isinstance(f, dict) else None
        if not isinstance(extra, dict) or not extra.get("ai_read"):
            continue
        readings = {k: v for k, v in extra.items() if k != "ai_read"} if "readings" in extra else None
        out.append({"ai_read": extra["ai_read"], "element": f.get("element"),
                    **({"readings": readings} if readings else {})})
    return out


def submit_versions(db: Session) -> int:
    slots = in_flight_limit() - _engine_in_flight(db)
    if slots <= 0 or queues.is_paused(db, queues.EDITS):
        return 0
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.status == "queued")
                          .order_by(DocumentVersion.created_at).limit(slots).with_for_update(skip_locked=True)).all()
    sent = 0
    for v in versions:
        base = db.get(DocumentVersion, v.based_on_id) if v.based_on_id else None
        base_pdf = db.get(JobFile, base.pdf_file_id) if base and base.pdf_file_id else None
        # An edit starts from the version it's based on; a corrected file is checked as uploaded, against that version
        src = db.get(JobFile, v.source_file_id) if v.source_file_id else base_pdf
        if src is None or v.job.files_deleted_at is not None or (v.source_file_id and base_pdf is None):
            _fail_version(db, v, "edit_rejected", "The version these edits were based on is no longer available.")
            db.commit()
            continue
        try:
            with tempfile.TemporaryFile() as tmp, tempfile.TemporaryFile() as ref:
                get_storage().get_to_file(src.s3_key, tmp)
                tmp.seek(0)
                if v.source_file_id:
                    get_storage().get_to_file(base_pdf.s3_key, ref)
                    ref.seek(0)
                v.engine_job_id = get_engine_client().submit_edit(safe_name(src.name), tmp, v.edits or [],
                                                                  review=v.review or "none",
                                                                  judge=_judge_for(db, v.job),
                                                                  reference=ref if v.source_file_id else None,
                                                                  validator=validator_flags(settings_store.load(db)),
                                                                  ai_read=carried_ai_read(base))
        except EngineUnavailable as e:
            log.warning("engine unavailable, version %s stays queued: %s", v.id, e)
            db.commit()
            break
        except EngineError as e:
            _fail_version(db, v, "edit_rejected", str(e))
            db.commit()
            continue
        v.status = "submitted"
        v.submitted_at = _now()
        if v.review_status == "queued":  # the review runs as part of this save
            v.review_status, v.review_engine_job_id, v.review_submitted_at = "running", v.engine_job_id, _now()
        db.commit()
        sent += 1
    db.commit()
    return sent


def poll_versions(db: Session) -> int:
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.status.in_(("submitted", "processing"))
                                                        | DocumentVersion.checking)
                          .order_by(DocumentVersion.submitted_at).with_for_update(skip_locked=True)).all()
    done = 0
    for v in versions:
        try:
            resp = _no_nul(get_engine_client().status(v.engine_job_id))
        except EngineUnavailable:
            break
        except EngineError as e:
            _fail_version(db, v, "engine_lost", str(e))
            db.commit()
            continue
        status = resp.get("status")
        if status == "processing":
            if v.submitted_at and _now() - v.submitted_at > JOB_TIMEOUT:
                _fail_version(db, v, "time_limit_exceeded", "no result in time")
            elif not v.checking and resp.get("partial") and v.replaces_id is None:
                try:
                    _open_early(db, v, resp["partial"])
                except EngineUnavailable:
                    db.rollback()
                    continue
            elif not v.checking:
                v.status = "processing"
            db.commit()
        elif status == "failed":
            err = resp.get("error") or {}
            _fail_version(db, v, err.get("error") or "failed", err.get("detail"))
            db.commit()
            done += 1
        elif status == "completed":
            try:
                _collect_version(db, v, resp)
            except EngineUnavailable:
                db.rollback()
                continue
            db.commit()
            done += 1
    db.commit()
    return done


def _version_names(db: Session, v: DocumentVersion) -> tuple[str, str]:
    """The version's PDF and tag map file names."""
    job = v.job
    stem = job.filename.rsplit(".", 1)[0]
    pdf_name = f"{stem}_corrected_{v.number}.pdf" if v.kind == "corrected" else f"{stem}_human_reviewed_{v.number}.pdf"
    return pdf_name, f"{stem}_tagmap_v{v.number}.json"


def _open_early(db: Session, v: DocumentVersion, partial: dict) -> None:
    """An edit job whose file and tag map are final, still checking (#428): the version can be opened now, and its
    verdict and findings follow when the checks finish. A version saved into another waits for the end, as it takes
    that one's place only then."""
    job = v.job
    prefix = job_prefix(job.group_id, job.user_id, job.id) + f"versions/{v.number}/"
    pdf_name, map_name = _version_names(db, v)
    pdf, tagmap = (partial.get("files") or {}).get("remediated_primary"), partial.get("tag_map")
    if not (isinstance(pdf, str) and pdf.startswith("/api/v1/jobs/") and isinstance(tagmap, str)
            and tagmap.startswith("/api/v1/jobs/")):
        return
    stored = [_store_engine_file(db, job, kind, path, name, prefix)
              for kind, path, name in (("reviewed_pdf", pdf, pdf_name), ("reviewed_tagmap", tagmap, map_name))]
    db.flush()
    v.pdf_file_id, v.tagmap_file_id = stored[0].id, stored[1].id
    v.status, v.checking = "ready", True
    audit.record(db, None, "version.open_early", actor_email="system", target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number})


def _collect_version(db: Session, v: DocumentVersion, resp: dict) -> None:
    job = v.job
    prefix = job_prefix(job.group_id, job.user_id, job.id) + f"versions/{v.number}/"
    stem = job.filename.rsplit(".", 1)[0]
    reports = resp.get("reports") or {}
    pdf_name = f"{stem}_corrected_{v.number}.pdf" if v.kind == "corrected" else f"{stem}_human_reviewed_{v.number}.pdf"
    # A version saved into another keeps that one's name, so its file keeps its file name too
    before = db.get(DocumentVersion, v.replaces_id) if v.replaces_id else None
    before_pdf = db.get(JobFile, before.pdf_file_id) if before is not None and before.pdf_file_id else None
    if before_pdf is not None:
        pdf_name = before_pdf.name
    ua = 2 if ((reports.get("comparison") or {}).get("after") or {}).get("pdfua_part") == 2 else 1  # #409
    wanted = [
        ("reviewed_pdf", (resp.get("files") or {}).get("remediated_primary"), pdf_name),
        ("reviewed_tagmap", (reports.get("tag_maps") or {}).get("primary"), f"{stem}_tagmap_v{v.number}.json"),
        ("reviewed_verapdf", (reports.get("verapdf_after") or {}).get("primary"),
         f"{stem}_verapdf_v{v.number}_ua{ua}.xml"),
    ]
    # Opened early (#428): its file and tag map are already stored, and haven't changed since
    have = {"reviewed_pdf": v.pdf_file_id, "reviewed_tagmap": v.tagmap_file_id} if v.checking else {}
    files = {}
    for kind, path, name in wanted:
        if have.get(kind):
            continue
        if isinstance(path, str) and path.startswith("/api/v1/jobs/") and name:
            files[kind] = _store_engine_file(db, job, kind, path, name, prefix)
    db.flush()
    v.pdf_file_id = have.get("reviewed_pdf") or (files["reviewed_pdf"].id if "reviewed_pdf" in files else None)
    v.tagmap_file_id = have.get("reviewed_tagmap") or (
        files["reviewed_tagmap"].id if "reviewed_tagmap" in files else None)
    v.checking = False
    v.verapdf_file_id = files["reviewed_verapdf"].id if "reviewed_verapdf" in files else None
    v.summary = version_summary(resp, "reviewed")
    v.status = "ready" if v.pdf_file_id else "failed"
    v.finished_at = _now()
    _take_place(db, v)
    if v.review_status == "running" and v.review_engine_job_id == v.engine_job_id:
        v.review_status = "done" if v.status == "ready" else "failed"
        v.reviewed_at = _now()
        v.summary["ai_review"] = {"kind": v.review, "at": v.reviewed_at.isoformat(), "by": v.review_requested_by}
    audit.record(db, None, "version.ready", actor_email="system", target_type="job", target_id=job.id,
                 group_id=job.group_id, details={"version": v.number, "compliant": v.summary.get("compliant")})


# --- AI reviews of saved versions --------------------------------------------------------------

REVIEW_FAILED = "The AI review couldn't be completed. The version and its earlier findings are unchanged."


def submit_reviews(db: Session) -> int:
    """Send queued reviews of saved versions to the engine: no edits, sampled AI review."""
    slots = in_flight_limit() - _engine_in_flight(db)
    if slots <= 0 or queues.is_paused(db, queues.REVIEWS):
        return 0
    pauses = queues.paused(db)
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.status == "ready",
                                                        DocumentVersion.review_status == "queued")
                          .order_by(DocumentVersion.created_at).limit(slots * 5).with_for_update(skip_locked=True)).all()
    sent = 0
    for v in versions:
        if sent >= slots:
            break
        # The review uses the document's AI level; while that's paused, the review waits
        if v.job.ai_tier and queues.service_key(v.job.ai_tier) in pauses:
            continue
        src = db.get(JobFile, v.pdf_file_id) if v.pdf_file_id else None
        if src is None or v.job.files_deleted_at is not None:
            v.review_status, v.review_error = "failed", "This version's file is no longer available."
            db.commit()
            continue
        try:
            with tempfile.TemporaryFile() as tmp:
                get_storage().get_to_file(src.s3_key, tmp)
                tmp.seek(0)
                v.review_engine_job_id = get_engine_client().submit_edit(safe_name(src.name), tmp, [],
                                                                         review=v.review or "sampled",
                                                                         judge=_judge_for(db, v.job),
                                                                         validator=validator_flags(
                                                                             settings_store.load(db)))
        except EngineUnavailable as e:
            log.warning("engine unavailable, review of version %s stays queued: %s", v.id, e)
            db.commit()
            break
        except EngineError as e:
            log.warning("engine refused review of version %s: %s", v.id, e)
            v.review_status, v.review_error = "failed", REVIEW_FAILED
            db.commit()
            continue
        v.review_status, v.review_submitted_at = "running", _now()
        db.commit()
        sent += 1
    return sent


def poll_reviews(db: Session) -> int:
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.status == "ready",
                                                        DocumentVersion.review_status == "running")
                          .with_for_update(skip_locked=True)).all()
    done = 0
    for v in versions:
        try:
            resp = _no_nul(get_engine_client().status(v.review_engine_job_id))
        except EngineUnavailable:
            break
        except EngineError:
            v.review_status, v.review_error = "failed", REVIEW_FAILED
            db.commit()
            continue
        status = resp.get("status")
        if status == "processing":
            if v.review_submitted_at and _now() - v.review_submitted_at > JOB_TIMEOUT:
                v.review_status, v.review_error = "failed", REVIEW_FAILED
            db.commit()
            continue
        if status == "failed":
            v.review_status, v.review_error = "failed", REVIEW_FAILED
        elif status == "completed":
            new = version_summary(resp, "reviewed")
            old = dict(v.summary or {})
            # Same file: keep what the save recorded (edits applied, visual check), replace the findings
            for key in ("edits_applied", "visual_identical", "warnings"):
                if key in old:
                    new[key] = old[key]
            v.reviewed_at = _now()
            new["ai_review"] = {"kind": v.review, "at": v.reviewed_at.isoformat(), "by": v.review_requested_by}
            v.summary = new
            v.review_status, v.review_error = "done", None
            audit.record(db, None, "version.reviewed", actor_email="system", target_type="job", target_id=v.job_id,
                         group_id=v.job.group_id, details={"version": v.number, "findings": new["findings_total"]})
        else:
            continue
        db.commit()
        done += 1
    return done


def _notify(db: Session, job: Job) -> None:
    """Queue the "done" or "failed" email for the outbox, and commit."""
    try:
        rs = settings_store.load(db)
        mail = _job_mail(job)
        mail.text += f"\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"
        outbox.queue(db, [mail], rs.mail_from_name, "job", about=f"job:{job.id}")
    except Exception:  # noqa: BLE001 - mail problems never block job processing
        log.exception("couldn't email %s about job %s", job.user.email, job.id)
    db.commit()


def sweep(db: Session) -> dict:
    """Delete files past the retention period or marked done (once the hold is over), and records past the records
    purge cycle."""
    runtime = settings_store.load(db)
    now = _now()
    file_cutoff = now - timedelta(days=runtime.file_retention_days)
    expired = db.scalars(select(Job).where(Job.files_deleted_at.is_(None), Job.created_at < file_cutoff,
                                           Job.status.not_in(ACTIVE_JOB_STATUSES))
                         .with_for_update(skip_locked=True).limit(500)).all()
    # Documents people marked done, once the hold is over
    early = [j for j in removal.due(db, runtime.early_removal_hold_hours, now) if j not in expired]
    objects, help_requests = removal.remove_files(db, list(expired) + early, now)
    for job in early:
        audit.record(db, None, "job.files_removed", actor_email="system", target_type="job", target_id=job.id,
                     group_id=job.group_id, details={"filename": job.filename, "reason": "marked done",
                                                     "requested_by": job.removal_requested_by})
    db.commit()

    record_cutoff = now - timedelta(days=runtime.record_retention_days)
    counts = {
        "files_jobs": len(expired),
        "files_jobs_marked_done": len(early),
        "files_objects": objects,
        "help_requests": help_requests,
        "audit_events": db.execute(delete(AuditEvent).where(AuditEvent.created_at < record_cutoff)).rowcount,
        "ledger_entries": db.execute(delete(QuotaLedger).where(QuotaLedger.created_at < record_cutoff)).rowcount,
        "jobs": db.execute(delete(Job).where(Job.created_at < record_cutoff,
                                             Job.files_deleted_at.is_not(None))).rowcount,
        "otp_challenges": db.execute(delete(OtpChallenge).where(OtpChallenge.created_at < now - timedelta(days=1))).rowcount,
        "sessions": db.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=30))).rowcount,
        "emails": outbox.purge(db, runtime.record_retention_days),
        "presence": notifications.purge_presence(db),
    }
    if any(counts.values()):
        audit.record(db, None, "records.purged", actor_email="system", details={
            **counts, "file_retention_days": runtime.file_retention_days,
            "record_retention_days": runtime.record_retention_days})
    db.commit()
    return counts


# --- loop ----------------------------------------------------------------------------------------

_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    s = get_portal_settings()
    if s.s3_create_bucket:
        get_storage().ensure_bucket()
    log.info("portal worker started; engine %s", s.engine_url)
    if s.metrics_enabled:
        metrics.serve_worker(s.worker_metrics_port)
    last_sweep = last_reports = last_beat = last_alerts = last_siem = 0.0
    while not _stop:
        started = time.monotonic()
        metrics.WORKER_BUSY.set(1)
        outcome = "ok"
        try:
            with system_session("worker") as db:
                submit_queued(db)
                poll_active(db)
                submit_versions(db)
                poll_versions(db)
                submit_reviews(db)
                poll_reviews(db)
                helpdesk.send_pending(db)
                if time.monotonic() - last_reports > 60:  # scheduled email reports: due ones go into the outbox
                    email_reports.send_due(db)
                    report_views.send_due(db)
                    notifications.send_digests(db)
                    last_reports = time.monotonic()
                outbox.send_queued(db)
                if time.monotonic() - last_sweep > s.worker_sweep_minutes * 60:
                    counts = sweep(db)
                    counts.update(helpdesk.remind_and_close(db))
                    counts.update(api_tokens.sweep(db))
                    counts["versions_replaced"] = purge_superseded(db)
                    last_sweep = time.monotonic()
                    if any(counts.values()):
                        log.info("purge: %s", counts)
                if time.monotonic() - last_beat > 30:  # for the API and the alerts (#334)
                    alerts.beat(db)
                    db.commit()
                    last_beat = time.monotonic()
            touch_alive()
        except Exception:  # noqa: BLE001 - keep the worker alive; the error is logged
            outcome = "failed"
            log.exception("worker tick failed")
        finally:
            metrics.WORKER_BUSY.set(0)
            metrics.WORKER_PASSES.labels(outcome).inc()
            metrics.WORKER_PASS_SECONDS.observe(time.monotonic() - started)
        # Alerts in a step of their own, so a pass that fails doesn't stop the alert about it (#334)
        if time.monotonic() - last_alerts > 60:
            last_alerts = time.monotonic()
            try:
                engine = get_engine_client()
                asked = hasattr(engine, "capacity")
                prom, firing = prometheus.get_prometheus(), None
                if prom is not None:  # the deployment's Prometheus, with MONITORING=on (#397)
                    try:
                        firing = prom.firing()
                    except prometheus.Unreachable as e:
                        log.warning("Prometheus not reached: %s", e)
                with system_session("worker alerts") as db:
                    alerts.evaluate(db, engine_asked=asked, engine_workers=engine.capacity() if asked else None,
                                    prom_asked=prom is not None, prom_firing=firing, scanner=_scanner())
                    pool_alerts.check(db)  # groups whose pool just ran low: their admins are told (#358)
                    security_alerts.evaluate(db)  # patterns in the audit log (#456)
                    db.commit()
                    outbox.send_queued(db)
            except Exception:  # noqa: BLE001
                log.exception("alert check failed")
        # The audit log to the campus SIEM (#456), in a step of its own: a collector that's slow or away never holds up
        # documents; it's retried with backoff and alerted when it falls an hour behind
        if siem.mode() != "off" and time.monotonic() - last_siem > 30:
            last_siem = time.monotonic()
            try:
                with system_session("worker siem") as db:
                    siem.export(db)
            except Exception:  # noqa: BLE001
                log.exception("SIEM export failed")
        time.sleep(s.worker_poll_seconds)
    log.info("portal worker stopped")


if __name__ == "__main__":
    run_forever()
