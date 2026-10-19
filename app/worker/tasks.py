from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import traceback

from celery.exceptions import SoftTimeLimitExceeded

from app.pipeline import protection
from app.pipeline.orchestrator import AIProviderUnavailable, JobOptions, run_remediation
from app.storage import JobStore, purge_expired_jobs
from app.validation.verapdf import job_validator
from app.verification.model import IntegrityError
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

# A worker that dies with a job (out of memory, a node drained) leaves it unacknowledged, and the queue hands it out
# again. One more start is fair (a deploy or a drained node isn't the document's fault); a job that took its worker
# down twice would only do it again, paying for its AI each time
MAX_STARTS = 2


# While a job runs, its worker says so every HEARTBEAT_S (a thread beside the work): a heartbeat older than
# HEARTBEAT_STALE_S on an unfinished job means the worker was lost (#315), and the status says so
HEARTBEAT_S = 30
HEARTBEAT_STALE_S = 120


@contextlib.contextmanager
def _heartbeat(store: JobStore):
    stop = threading.Event()

    def beat() -> None:
        while not stop.is_set():
            try:
                store.beat()
            except OSError:  # a missed beat isn't the job failing
                pass
            stop.wait(HEARTBEAT_S)
    t = threading.Thread(target=beat, name=f"heartbeat-{store.job_id[:8]}", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def _not_again(store: JobStore, job_id: str) -> dict | None:
    """None when the job should run; else what the task returns without running it: a job handed out again after it
    finished or was stopped, or one whose worker was lost on every start so far."""
    if store.read_result() is not None or store.read_error() is not None:
        log.info("job %s already finished or stopped; not run again", job_id)
        return {"job_id": job_id, "status": "skipped"}
    starts = store.count_attempt()
    if starts > MAX_STARTS:
        log.error("job %s: its worker was lost on each of %d starts; failed", job_id, starts - 1)
        store.write_error({"error": "worker_lost", "detail": "The worker processing this job stopped unexpectedly "
                           f"on each of {starts - 1} tries (often out of memory)."})
        return {"job_id": job_id, "status": "failed", "error": "worker_lost"}
    if starts > 1:
        log.warning("job %s: started again after its worker was lost (start %d of %d)", job_id, starts, MAX_STARTS)
    return None


@celery_app.task(bind=True, name="app.worker.tasks.remediate")
def remediate(self, job_id: str) -> dict:
    store = JobStore(job_id)
    if (done := _not_again(store, job_id)) is not None:
        return done
    meta = store.read_meta() or {}
    options = JobOptions.from_meta(meta)

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001 - progress reporting must never fail a job
            log.debug("progress update failed", exc_info=True)

    try:
        info = protection.inspect(store.input_pdf)
        with _heartbeat(store), job_validator(meta.get("flags")):
            result = asyncio.run(run_remediation(store, options, progress))
        protection.finish(store.output_dir, result, info)  # its restrictions back on the outputs (#339)
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
    if (done := _not_again(store, job_id)) is not None:
        return done
    meta = store.read_meta() or {}
    options = JobOptions.from_meta(meta)

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001
            log.debug("progress update failed", exc_info=True)

    try:
        info = protection.inspect(store.input_pdf)
        with _heartbeat(store), job_validator(meta.get("flags")):
            result = asyncio.run(run_adobe(store, options, progress))
        protection.finish(store.output_dir, result, info)
        store.write_result(result)
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
    if (done := _not_again(store, job_id)) is not None:
        return done

    def progress(stage: str, pct: int) -> None:
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "percent": pct})
        except Exception:  # noqa: BLE001
            log.debug("progress update failed", exc_info=True)

    try:
        info = protection.inspect(store.input_pdf)  # a saved version keeps the restrictions it was given
        with _heartbeat(store), job_validator((store.read_meta() or {}).get("flags")):
            result = asyncio.run(run_edit(store, progress))
        protection.finish(store.output_dir, result, info)
        store.write_result(result)
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
