from __future__ import annotations

import asyncio
import logging
import traceback

from celery.exceptions import SoftTimeLimitExceeded

from app.pipeline.orchestrator import AIProviderUnavailable, JobOptions, run_remediation
from app.storage import JobStore, purge_expired_jobs
from app.validation.verapdf import job_validator
from app.verification.model import IntegrityError
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.worker.tasks.remediate")
def remediate(self, job_id: str) -> dict:
    store = JobStore(job_id)
    meta = store.read_meta() or {}
    options = JobOptions.from_meta(meta)

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001 - progress reporting must never fail a job
            log.debug("progress update failed", exc_info=True)

    try:
        with job_validator(meta.get("flags")):
            result = asyncio.run(run_remediation(store, options, progress))
        store.write_result(result)
        return {"job_id": job_id, "status": "completed"}
    except AIProviderUnavailable as exc:
        # Expected, fully reported failure: one log line, no traceback.
        log.error("job %s failed: %s", job_id, exc)
        store.write_error({"error": "ai_provider_unavailable", "detail": str(exc)})
        return {"job_id": job_id, "status": "failed", "error": "ai_provider_unavailable"}
    except IntegrityError as exc:
        # The engine produced a structurally broken tag tree: fail the job, keep the outputs as evidence.
        log.error("job %s failed tag integrity: %s", job_id, exc)
        store.write_error({"error": "tag_integrity_failed", "detail": str(exc), "output": exc.label,
                           "failures": [f.to_dict() for f in exc.failures[:100]]})
        return {"job_id": job_id, "status": "failed", "error": "tag_integrity_failed"}
    except SoftTimeLimitExceeded:
        store.write_error({"error": "time_limit_exceeded", "detail": "Job exceeded the processing time limit."})
        raise
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded for the poller
        log.exception("job %s failed", job_id)
        err = {"error": type(exc).__name__, "detail": str(exc)}
        if options.debug:
            err["traceback"] = traceback.format_exc()
        store.write_error(err)
        raise
    finally:
        if not options.debug:
            store.cleanup_work()


@celery_app.task(bind=True, name="app.worker.tasks.remediate_adobe")
def remediate_adobe(self, job_id: str) -> dict:
    """Tag with Adobe PDF Services. Runs on its own queue ("adobe") so Adobe's pace never holds up
    the standard route, and so only workers configured for Adobe pick these jobs up."""
    from app.adobe.client import AdobeError
    from app.pipeline.adobe_job import adobe_error, run_adobe

    store = JobStore(job_id)
    meta = store.read_meta() or {}
    options = JobOptions.from_meta(meta)

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001
            log.debug("progress update failed", exc_info=True)

    try:
        with job_validator(meta.get("flags")):
            store.write_result(asyncio.run(run_adobe(store, options, progress)))
        return {"job_id": job_id, "status": "completed"}
    except AdobeError as exc:
        log.error("adobe job %s failed: %s", job_id, exc)
        err = adobe_error(exc)
        store.write_error(err)
        return {"job_id": job_id, "status": "failed", "error": err["error"]}
    except SoftTimeLimitExceeded:
        store.write_error({"error": "time_limit_exceeded", "detail": "Job exceeded the processing time limit."})
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("adobe job %s failed", job_id)
        store.write_error({"error": type(exc).__name__, "detail": str(exc)})
        raise
    finally:
        if not options.debug:
            store.cleanup_work()


@celery_app.task(bind=True, name="app.worker.tasks.edit_tags")
def edit_tags(self, job_id: str) -> dict:
    """Apply people's tag edits (from the portal's tag manager) and validate the result."""
    from app.pipeline.edit_job import run_edit
    from app.tagging.editor import EditError

    store = JobStore(job_id)

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001
            log.debug("progress update failed", exc_info=True)

    try:
        with job_validator((store.read_meta() or {}).get("flags")):
            store.write_result(asyncio.run(run_edit(store, progress)))
        return {"job_id": job_id, "status": "completed"}
    except EditError as exc:
        store.write_error({"error": "edit_rejected", "detail": str(exc)})
        return {"job_id": job_id, "status": "failed", "error": "edit_rejected"}
    except IntegrityError as exc:
        log.error("edit job %s failed tag integrity: %s", job_id, exc)
        store.write_error({"error": "tag_integrity_failed", "detail": str(exc),
                           "failures": [f.to_dict() for f in exc.failures[:100]]})
        return {"job_id": job_id, "status": "failed", "error": "tag_integrity_failed"}
    except Exception as exc:  # noqa: BLE001
        log.exception("edit job %s failed", job_id)
        store.write_error({"error": type(exc).__name__, "detail": str(exc)})
        raise
    finally:
        store.cleanup_work()


@celery_app.task(name="app.worker.tasks.purge_expired")
def purge_expired() -> int:
    return purge_expired_jobs()
