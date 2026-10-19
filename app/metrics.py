"""The engine's metrics for Prometheus (#397).

The API answers /metrics on its own port with what's waiting in Redis: the documents queued for the workers, for the
Adobe worker, and the pages waiting for the music reader. Each worker serves its own on a small port of its own
(WORKER_METRICS_PORT): how many documents it's working on, out of how many it can take, which is how the admin view
tells a working pod from an idle one. Tasks run in child processes; the worker's own process knows which are running.
"""
from __future__ import annotations

import logging
import os

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

log = logging.getLogger(__name__)
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
QUEUES = {"engine": "celery", "adobe": "adobe", "music": "omr:requests"}


class _Queues:
    def __init__(self, client):
        self.client = client

    def collect(self):
        g = GaugeMetricFamily("engine_queue_length", "Items waiting in Redis, by queue (the workers', the Adobe "
                              "worker's, the music reader's pages)", labels=["queue"])
        up = 1
        for name, key in QUEUES.items():
            try:
                g.add_metric([name], self.client.llen(key))
            except Exception:  # noqa: BLE001 - Redis down: the rest still answers, and engine_redis_up says so
                up = 0
        yield g
        yield GaugeMetricFamily("engine_redis_up", "1 when Redis answered", value=up)


def api_metrics(client) -> bytes:
    reg = CollectorRegistry()
    reg.register(_Queues(client))
    return generate_latest(reg)


class _Worker:
    """A Celery worker's own: busy from the requests its process is running, places from its pool."""

    def __init__(self, consumer):
        self.consumer = consumer

    def collect(self):
        from celery.worker import state
        yield GaugeMetricFamily("engine_worker_busy", "Documents this worker is processing now",
                                value=len(state.active_requests))
        places = getattr(getattr(self.consumer, "pool", None), "num_processes", None) or getattr(
            getattr(self.consumer, "controller", None), "concurrency", None) or 0
        yield GaugeMetricFamily("engine_worker_places", "Documents this worker can process at once", value=places)


def serve_worker(consumer) -> None:
    """Start a worker's metrics server (a thread in its own process); a port that can't be opened is logged."""
    from prometheus_client import start_http_server
    port = int(os.environ.get("WORKER_METRICS_PORT", "9464"))
    reg = CollectorRegistry()
    reg.register(_Worker(consumer))
    try:
        start_http_server(port, registry=reg)
    except OSError as exc:
        log.warning("worker metrics not served on port %s: %s", port, exc)
