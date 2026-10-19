from __future__ import annotations

import json
import secrets
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pikepdf
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import ValidationError

from app.ai.registry import available_providers
from app.api.schemas import Files, JobResponse, Metrics, RemediationFlags, Reports, SubmitResponse
from app.config import get_settings
from app.storage import JobStore, safe_stem

router = APIRouter(prefix="/api/v1")

_CHUNK = 1024 * 1024


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().service_api_key
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def _check_pdf(path) -> int:
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except pikepdf.PasswordError:
        raise HTTPException(status_code=422, detail="PDF is password protected")
    except pikepdf.PdfError as exc:
        raise HTTPException(status_code=422, detail=f"PDF could not be parsed: {exc}")


@router.post("/remediate", response_model=SubmitResponse, status_code=202, dependencies=[Depends(require_api_key)])
async def submit_remediation(
    file: UploadFile = File(...),
    ai_provider: str = Form("none"),
    ai_model: str | None = Form(None),
    ai_tier: str | None = Form(None, max_length=64),
    judge_model: str | None = Form(None, max_length=200),
    flags: str = Form("{}"),
    route: str = Form("standard", pattern="^(standard|adobe)$"),
):
    """route: "standard" (this engine's tagger) or "adobe" (Adobe PDF Services Autotag, when switched on)."""
    settings = get_settings()
    if route == "adobe" and not settings.adobe_enabled:
        raise HTTPException(status_code=422, detail="the adobe route isn't switched on for this engine")
    try:
        parsed_flags = RemediationFlags.model_validate(json.loads(flags or "{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid flags: {exc}")
    if route == "adobe" and parsed_flags.report_only:
        raise HTTPException(status_code=422, detail="report_only uses the standard route; nothing is sent to Adobe")

    provider = ai_provider.strip().lower()
    if provider not in available_providers():
        raise HTTPException(status_code=422, detail=f"unknown ai_provider '{ai_provider}'; available: {available_providers()}")

    job_id = str(uuid.uuid4())
    store = JobStore(job_id)
    store.create()

    # Stream to disk with a size cap; never hold the whole upload in memory.
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with store.input_pdf.open("wb") as out:
            first = True
            while chunk := await file.read(_CHUNK):
                if first and not chunk.lstrip()[:5].startswith(b"%PDF-"):
                    raise HTTPException(status_code=415, detail="file is not a PDF")
                first = False
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"file exceeds {settings.max_upload_mb} MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="empty file")
        page_count = await run_in_threadpool(_check_pdf, store.input_pdf)
        # The portal keeps to its own page cap, but this API trusts whoever holds its key: a document of
        # tens of thousands of pages would hold a worker until the time limit, so the engine has a cap of its own
        if settings.max_pages and page_count > settings.max_pages:
            raise HTTPException(status_code=422, detail=f"the document has {page_count} pages; at most "
                                                        f"{settings.max_pages} are accepted (MAX_PAGES)")
    except HTTPException:
        shutil.rmtree(store.dir, ignore_errors=True)
        raise

    store.write_meta(
        {
            "job_id": job_id,
            "filename": file.filename,
            "stem": safe_stem(file.filename or "document.pdf"),
            "size_bytes": size,
            "page_count": page_count,
            "ai_provider": provider,
            "ai_model": ai_model,
            "ai_tier": ai_tier,
            "judge_model": judge_model,
            "route": route,
            "flags": parsed_flags.model_dump(),
            "submitted_at": time.time(),
        }
    )

    from app.worker.tasks import remediate, remediate_adobe  # local import keeps API importable without a broker

    (remediate_adobe if route == "adobe" else remediate).apply_async(args=[job_id], task_id=job_id)
    return SubmitResponse(job_id=job_id)


@router.get("/ai/models", dependencies=[Depends(require_api_key)])
async def ai_models():
    """Models the AI gateway serves, for the portal's AI tier picker."""
    from app.ai.base import AIError
    from app.ai.gateway import list_models

    try:
        return {"models": await list_models()}
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def worker_capacity(timeout: float = 1.0) -> dict:
    """The workers answering now and how many documents they process at once between them (None when none answer,
    or the queue can't be reached)."""
    from app.worker.celery_app import celery_app

    try:
        stats = celery_app.control.inspect(timeout=timeout).stats() or {}
    except Exception:  # noqa: BLE001 - the queue being down mustn't break the caller
        return {"workers": None, "documents_at_once": None}
    at_once = sum(int((s.get("pool") or {}).get("max-concurrency") or 0) for s in stats.values())
    return {"workers": len(stats), "documents_at_once": at_once or None}


@router.get("/capacity", dependencies=[Depends(require_api_key)])
async def capacity():
    """How much the engine can process at once, so the portal hands over as many documents as there are places
    (the number of workers changes when they're scaled with the queue)."""
    return await run_in_threadpool(worker_capacity)


@router.post("/edit", response_model=SubmitResponse, status_code=202, dependencies=[Depends(require_api_key)])
async def submit_edit(file: UploadFile = File(...), edits: str = Form(...), verify_ai: bool = Form(True),
                      review: str = Form("none", pattern="^(none|sampled|full)$"),
                      judge_model: str | None = Form(None, max_length=200),
                      reference: UploadFile | None = File(None),
                      validator: str | None = Form(None, pattern="^(service|command_line)$"),
                      verapdf_ua2: bool = Form(True)):
    """Apply tag edits to a remediated PDF and validate the result (the portal's tag manager).
    review=sampled also runs the AI tag review (flagged items plus a sample) over the whole result, and
    review=full reviews every figure;
    with no edits that re-reviews the file as it is.
    reference: the version a corrected file replaces. With it, no edits are needed (the file is checked as it
    is) and the visual comparison is made against the reference instead of the file itself."""
    from app.tagging.editor import EditError, validate_ops

    try:
        parsed = json.loads(edits)
        ops = [] if parsed == [] and (review != "none" or reference is not None) else validate_ops(parsed)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="edits must be a JSON list")
    except EditError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    job_id = str(uuid.uuid4())
    store = JobStore(job_id)
    store.create()
    size = await _receive_pdf(file, store)
    if reference is not None:
        await _receive_reference(reference, store)
    store.write_meta({"job_id": job_id, "kind": "edit", "filename": file.filename,
                      "stem": safe_stem(file.filename or "document.pdf"), "size_bytes": size,
                      "edits": ops, "verify_ai": verify_ai, "review": review, "judge_model": judge_model,
                      "reference": reference is not None, "submitted_at": time.time(),
                      "flags": {**({"validator": validator} if validator else {}), "verapdf_ua2": verapdf_ua2}})
    from app.worker.tasks import edit_tags

    edit_tags.apply_async(args=[job_id], task_id=job_id)
    return SubmitResponse(job_id=job_id)


async def _receive_reference(file: UploadFile, store: JobStore) -> None:
    """The version a corrected file replaces, kept only for the visual comparison."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit or not data.lstrip()[:5].startswith(b"%PDF-"):
        shutil.rmtree(store.dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="the reference must be a PDF within the size limit")
    store.work_dir.mkdir(parents=True, exist_ok=True)
    (store.work_dir / "reference.pdf").write_bytes(data)


async def _receive_pdf(file: UploadFile, store: JobStore) -> int:
    limit = get_settings().max_upload_mb * 1024 * 1024
    size = 0
    try:
        with store.input_pdf.open("wb") as out:
            first = True
            while chunk := await file.read(_CHUNK):
                if first and not chunk.lstrip()[:5].startswith(b"%PDF-"):
                    raise HTTPException(status_code=415, detail="file is not a PDF")
                first = False
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"file exceeds {get_settings().max_upload_mb} MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="empty file")
        await run_in_threadpool(_check_pdf, store.input_pdf)
    except HTTPException:
        shutil.rmtree(store.dir, ignore_errors=True)
        raise
    return size


@router.get("/jobs/{job_id}", response_model=JobResponse, response_model_exclude_unset=True,
            dependencies=[Depends(require_api_key)])
async def get_job(job_id: str, format: str = Query("json", pattern="^(json|markdown|md)$",
                                                   description="json (default) or markdown")):
    try:
        store = JobStore(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found")
    meta = store.read_meta()
    if meta is None:
        raise HTTPException(status_code=404, detail="job not found")
    if format in ("markdown", "md"):
        return _markdown(store, meta)

    if (err := store.read_error()) is not None:
        return JobResponse(job_id=job_id, status="failed", error=err)

    result = store.read_result()
    if result is None:
        # A task that ended without a result or an error on file was killed before it could write one (the hard
        # time limit, the worker running out of memory, a stop request): say so, or the caller waits for ever
        ended = await run_in_threadpool(_ended_without_result, job_id)
        if ended is not None:
            store.write_error(ended)
            return JobResponse(job_id=job_id, status="failed", error=ended)
        return JobResponse(job_id=job_id, status="processing", progress=_lost(store) or _progress(job_id))

    b64 = bool(meta.get("flags", {}).get("return_base64"))
    ref = lambda name: store.render_file_ref(name, b64)  # noqa: E731
    files, reports = result.get("files", {}), result.get("reports", {})
    after = reports.get("verapdf_after") or {}
    return JobResponse(
        job_id=job_id,
        status="completed",
        files=Files(
            remediated_primary=ref(files.get("remediated_primary")),
            remediated_visual_fallback=ref(files.get("remediated_visual_fallback")),
            enhanced=ref(files.get("enhanced")),
        ),
        metrics=Metrics.model_validate(result.get("metrics", {})),
        reports=Reports(
            verapdf_compliance_report=ref(after.get("primary") or reports.get("verapdf_before")),
            verapdf_before=ref(reports.get("verapdf_before")),
            verapdf_after={k: ref(v) for k, v in after.items()} if after else None,
            comparison=reports.get("comparison"),
            remediation_summary=reports.get("remediation_summary", ""),
            ai_findings=reports.get("ai_findings") or None,
            full_report=ref(result.get("full_report")),
            markdown_report=ref(result.get("markdown_report")),
            report_pdf=ref(result.get("report_pdf")),
            tag_verification=reports.get("tag_verification"),
            tag_maps={k: ref(v) for k, v in (reports.get("tag_maps") or {}).items()} or None,
            music_files=ref(reports.get("music_files")),
            music_score=ref(reports.get("music_score")),
            math_files=ref(reports.get("math_files")),
            math_list=ref(reports.get("math_list")),
            math_nemeth=ref(reports.get("math_nemeth")),
            math_ueb=ref(reports.get("math_ueb")),
            edits_applied=reports.get("edits_applied"),
            adobe_reports=[ref(n) for n in reports.get("adobe_reports") or []] or None,
        ),
        warnings=result.get("warnings") or None,
    )


def _markdown(store: JobStore, meta: dict) -> PlainTextResponse:
    from app.reports.markdown import render_job_markdown

    err, result = store.read_error(), store.read_result()
    status = "failed" if err is not None else "completed" if result is not None else "processing"
    md = render_job_markdown(
        store.job_id, meta, status, result=result, error=err,
        progress=_progress(store.job_id) if status == "processing" else None,
        ref=lambda name: store.render_file_ref(name, as_base64=False),  # links, never inline base64
    )
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


def _ended_without_result(job_id: str) -> dict | None:
    """The error to record for a task the queue says is over (failed or stopped), else None."""
    try:
        from app.worker.celery_app import celery_app

        res = celery_app.AsyncResult(job_id)
        state, info = res.state, res.info
    except Exception:  # noqa: BLE001 - the queue being unreachable isn't the job failing
        return None
    if state == "REVOKED":
        return {"error": "stopped", "detail": "The job was stopped before it finished."}
    if state != "FAILURE":
        return None
    name = type(info).__name__
    if "TimeLimit" in name:
        return {"error": "time_limit_exceeded", "detail": "Job exceeded the processing time limit."}
    if "WorkerLost" in name:
        return {"error": "worker_lost", "detail": "The worker processing this job stopped unexpectedly (often out of memory)."}
    return {"error": name or "failed", "detail": str(info)[:500]}


@router.post("/jobs/{job_id}/stop", dependencies=[Depends(require_api_key)])
async def stop_job(job_id: str):
    """Stop a job that's waiting or in progress. Its failure is on file at once (error "stopped"), and the worker
    process running it is ended so it stops using a place. A finished job is left as it is."""
    try:
        store = JobStore(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found")
    if store.read_meta() is None:
        raise HTTPException(status_code=404, detail="job not found")
    if store.read_result() is not None or store.read_error() is not None:
        return {"job_id": job_id, "stopped": False}
    store.write_error({"error": "stopped", "detail": "The job was stopped before it finished."})

    def revoke() -> None:
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")
    try:
        await run_in_threadpool(revoke)
    except Exception as exc:  # noqa: BLE001 - the job is marked stopped either way; say the worker wasn't reached
        return {"job_id": job_id, "stopped": True, "worker": f"not reached: {exc}"}
    return {"job_id": job_id, "stopped": True}


def _lost(store: JobStore) -> dict | None:
    """A job whose worker stopped beating (#315): it was killed, and the queue will hand the job out again once its
    visibility timeout has passed since it was taken (TASK_TIME_LIMIT_S + 15 min), once more at most (MAX_STARTS). Without
    this, the last progress the worker reported stayed on show, as if it were still working."""
    import time as _time

    from app.worker.tasks import HEARTBEAT_STALE_S, MAX_STARTS

    beat, attempts = store.last_beat(), store.read_attempts() or {}
    if beat is None or _time.time() - beat < HEARTBEAT_STALE_S:
        return None
    starts = int(attempts.get("starts") or 0)
    taken = float(attempts.get("at") or beat)
    retry = taken + get_settings().task_time_limit_s + 900
    return {"stage": "worker_lost", "lost_at": _iso(beat), "starts": starts,
            "will_retry": starts < MAX_STARTS, "retry_after": _iso(retry) if starts < MAX_STARTS else None}


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _progress(job_id: str) -> dict | None:
    try:
        from app.worker.celery_app import celery_app

        res = celery_app.AsyncResult(job_id)
        if res.state == "PROGRESS" and isinstance(res.info, dict):
            return res.info
        return {"stage": res.state.lower()}
    except Exception:  # noqa: BLE001 - progress is best effort
        return None


@router.get("/jobs/{job_id}/files/{name}", dependencies=[Depends(require_api_key)])
async def download_file(job_id: str, name: str):
    try:
        store = JobStore(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found")
    path = store.output_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="file not found")
    media = {".pdf": "application/pdf", ".xml": "application/xml", ".md": "text/markdown; charset=utf-8",
             ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(
        path.suffix, "application/json")
    return FileResponse(path, media_type=media, filename=name)


@router.post("/classify", dependencies=[Depends(require_api_key)])
async def classify_document(file: UploadFile = File(...), kinds: str = Form(..., max_length=50_000),
                            backend: str = Form(..., pattern="^(jev|gateway)$"),
                            model: str | None = Form(None, max_length=200), pages: int = Form(3, ge=1, le=10)):
    """Suggest the kind of document from its first pages' text (no images): one of `kinds` (JSON list of
    {key, label, description}) or "general". Answers at once; nothing is kept."""
    from app.ai.base import AIError
    from app.classify import classify
    try:
        offered = [{"key": str(k["key"]), "label": str(k["label"]), "description": k.get("description")}
                   for k in json.loads(kinds)]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="kinds must be a JSON list of {key, label, description}")
    tmp = Path(tempfile.mkdtemp(prefix="classify-"))
    try:
        path = tmp / "in.pdf"
        size, limit = 0, get_settings().max_upload_mb * 1024 * 1024
        with path.open("wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"file exceeds {get_settings().max_upload_mb} MB")
                out.write(chunk)
        await run_in_threadpool(_check_pdf, path)
        try:
            return await classify(path, offered, backend, model, pages, file.filename or "")
        except AIError as exc:
            raise HTTPException(status_code=502, detail=f"classifier_unavailable: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/describe", dependencies=[Depends(require_api_key)])
async def describe_area(file: UploadFile = File(...), page: int = Form(..., ge=1), bbox: str = Form(..., max_length=200),
                        ai_provider: str = Form("gateway", max_length=40), ai_model: str | None = Form(None, max_length=200),
                        long: bool = Form(False)):
    """Alt text for an area of a page, from the AI: the area (bbox, JSON [x0, y0, x1, y1] in page units, as the tag
    map gives them) is drawn and sent with the text inside it. long: the room a scheme's steps need. Answers at once
    with {alt, kind, decorative, latex, model, cost_usd, prompt_tokens, completion_tokens}; nothing is kept."""
    import pymupdf as fitz

    from app.ai.base import AIError
    from app.ai.registry import available_providers, get_provider
    from app.pipeline.analyzer import render_png
    try:
        box = [float(v) for v in json.loads(bbox)]
        if len(box) != 4 or box[2] - box[0] < 1 or box[3] - box[1] < 1:
            raise ValueError
    except (json.JSONDecodeError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="bbox must be a JSON list [x0, y0, x1, y1] with some width and height")
    if ai_provider not in available_providers() or ai_provider == "none":
        raise HTTPException(status_code=422, detail=f"unknown AI provider: {ai_provider}")
    tmp = Path(tempfile.mkdtemp(prefix="describe-"))
    try:
        path = tmp / "in.pdf"
        size, limit = 0, get_settings().max_upload_mb * 1024 * 1024
        with path.open("wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"file exceeds {get_settings().max_upload_mb} MB")
                out.write(chunk)
        await run_in_threadpool(_check_pdf, path)

        def crop() -> tuple[bytes, str]:
            with fitz.open(path) as doc:
                if page > doc.page_count:
                    raise HTTPException(status_code=422, detail=f"page {page} doesn't exist")
                png = render_png(doc, page - 1, clip=tuple(box), max_px=1200)
                # What's written inside the area (labels, a caption) helps the AI name what's drawn
                words = doc[page - 1].get_text("text", clip=fitz.Rect(box)).strip()
            return png, " ".join(words.split())[:2000]
        try:
            png, context = await run_in_threadpool(crop)
        except ValueError as exc:  # the box is off the page
            raise HTTPException(status_code=422, detail=str(exc))
        ai = get_provider(ai_provider, ai_model)
        try:
            out = await ai.describe_image(png, page - 1, context, long=long)
        except AIError as exc:
            raise HTTPException(status_code=502, detail=f"ai_unavailable: {exc}")
        return {"alt": out.get("alt") or "", "kind": out.get("kind"), "decorative": bool(out.get("decorative")),
                "latex": out.get("latex"), "model": getattr(ai, "model", ai_model),
                "cost_usd": round(ai.usage.cost_usd, 6), "prompt_tokens": ai.usage.prompt_tokens,
                "completion_tokens": ai.usage.completion_tokens}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """For the deployment's Prometheus (#397): what's waiting in Redis. Only the portal and Prometheus may reach the
    engine's API (network policies); nothing here is a document's."""
    import redis
    from fastapi.responses import Response

    from app.metrics import CONTENT_TYPE, api_metrics
    client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
    return Response(await run_in_threadpool(api_metrics, client), media_type=CONTENT_TYPE)


@router.get("/live")
async def live():
    """For the liveness probe (#335): the process answers. Redis and veraPDF aren't checked here; /health does."""
    return {"status": "alive"}


@router.get("/health")
async def health():
    from app.validation.verapdf import service_version, verapdf_version

    status: dict = {"status": "ok"}
    try:
        import redis

        redis.Redis.from_url(get_settings().redis_url, socket_timeout=2).ping()
        status["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"unavailable: {exc}"
        status["status"] = "degraded"
    version = await run_in_threadpool(verapdf_version)
    status["verapdf"] = version or "unavailable"
    url = get_settings().verapdf_url.rstrip("/")
    service = None
    if url:
        service_version.cache_clear()  # the service restarts (hourly): ask each time
        service = await run_in_threadpool(service_version, url)
        status["verapdf_service"] = service or "unavailable (checks use the command line)"
    if not version and not service:
        status["status"] = "degraded"
    status["ai_providers"] = available_providers()
    s = get_settings()
    status["adobe"] = {"enabled": s.adobe_enabled, "configured": bool(s.adobe_client_id and s.adobe_client_secret)}
    return status
