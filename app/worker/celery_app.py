from celery import Celery

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
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
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
