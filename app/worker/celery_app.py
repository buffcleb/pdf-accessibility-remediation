import os
from pathlib import Path

from celery import Celery, bootsteps

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "pdf_remediation",
    broker=settings.redis_url,
    backend=settings.celery_result_backend or settings.redis_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    # Long AI/vision jobs: one at a time per worker process, re-queued if the worker dies.
    task_acks_late=True,
    # A task not acknowledged within this time is handed out again (a lost worker's). Redis's default is an hour; a
    # long document runs up to the time limit, and would get a second copy beside the first
    broker_transport_options={"visibility_timeout": settings.task_time_limit_s + 900},
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_memory_per_child=settings.worker_max_memory_mb * 1024,  # KiB
    task_time_limit=settings.task_time_limit_s,
    task_soft_time_limit=max(60, settings.task_time_limit_s - 60),
    result_expires=settings.job_ttl_hours * 3600,
    broker_connection_retry_on_startup=True,
    # Adobe jobs wait on Adobe's rate limits; they have their own queue and worker (celery -Q adobe).
    task_routes={"app.worker.tasks.remediate_adobe": {"queue": "adobe"}},
    beat_schedule={
        "purge-expired-jobs": {
            "task": "app.worker.tasks.purge_expired",
            "schedule": 3600.0,
        }
    },
)


# Liveness (#335): the worker's own event loop touches this file every HEARTBEAT_S while it's connected and taking
# tasks; the pod's liveness probe restarts a worker whose file has gone stale (hung, or cut off from Redis for minutes).
# A long document doesn't stop it: tasks run in child processes, and the loop goes on beside them.
ALIVE_FILE = Path(os.environ.get("WORKER_ALIVE_FILE", "/tmp/worker.alive"))
HEARTBEAT_S = 30


def touch_alive(path: Path | None = None) -> None:
    try:
        (path or ALIVE_FILE).touch()
    except OSError:
        pass  # a read-only or missing /tmp: the probe then restarts it, which says so


class Heartbeat(bootsteps.StartStopStep):
    requires = {"celery.worker.consumer.tasks:Tasks"}  # once it's consuming

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.tref = None

    def start(self, c):
        touch_alive()
        self.tref = c.timer.call_repeatedly(HEARTBEAT_S, touch_alive, priority=10)
        if os.environ.get("WORKER_METRICS", "on") != "off":  # for Prometheus (#397): busy or idle, and its places
            from app.metrics import serve_worker
            serve_worker(c)

    def stop(self, c):
        if self.tref is not None:
            self.tref.cancel()
            self.tref = None


celery_app.steps["consumer"].add(Heartbeat)
