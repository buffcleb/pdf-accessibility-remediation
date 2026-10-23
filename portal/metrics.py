"""The portal's metrics for Prometheus (#397), at /metrics on the API's own port (outside /api/, so neither the web
app's nginx nor the API-origin Ingress passes it on; the network policies let only Prometheus in).

Requests are counted by route as they're served. The rest is read from the database each time Prometheus asks (every
30 seconds): documents by status and how long the oldest has waited, documents finished, pages charged, the worker's
last pass, open alerts, queued email and each group's pool. These are small, indexed queries.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from sqlalchemy import func, select

from portal.models import ACTIVE_JOB_STATUSES, AlertState, Job, MailOutbox, WorkerBeat

REGISTRY = CollectorRegistry()
REQUESTS = Counter("portal_http_requests", "API requests served", ["method", "route", "status"], registry=REGISTRY)
LATENCY = Histogram("portal_http_request_seconds", "Time to answer an API request", ["method", "route"],
                    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30), registry=REGISTRY)
# Uploads scanned for malware (#455), by outcome (clean, blocked, unavailable), and how long a scan takes
MALWARE_SCANS = Counter("portal_malware_scans", "Uploads scanned for malware", ["outcome"], registry=REGISTRY)
MALWARE_SCAN_SECONDS = Histogram("portal_malware_scan_seconds", "Time to scan an upload for malware",
                                 buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120), registry=REGISTRY)
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def observe(method: str, route: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method, route, str(status)).inc()
    LATENCY.labels(method, route).observe(seconds)


class _Database:
    """What the database says, read at each scrape."""

    def __init__(self, sessions):
        self.sessions = sessions

    def collect(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            by_status = dict(db.execute(select(Job.status, func.count()).where(Job.status.in_(ACTIVE_JOB_STATUSES))
                                        .group_by(Job.status)).all())
            g = GaugeMetricFamily("portal_documents", "Documents not finished, by status", labels=["status"])
            for s in ACTIVE_JOB_STATUSES:
                g.add_metric([s], by_status.get(s, 0))
            yield g
            oldest = db.scalar(select(func.min(Job.created_at))
                               .where(Job.status.in_(("queued", "submitted", "processing"))))
            yield GaugeMetricFamily("portal_queue_oldest_seconds", "How long the oldest document in the queue has "
                                    "waited (0 when none is waiting)",
                                    value=(now - oldest).total_seconds() if oldest else 0)
            done = dict(db.execute(select(Job.status, func.count())
                                   .where(Job.status.in_(("completed", "failed", "canceled")))
                                   .group_by(Job.status)).all())
            c = CounterMetricFamily("portal_documents_finished", "Documents finished, by result", labels=["result"])
            for s in ("completed", "failed", "canceled"):
                c.add_metric([s], done.get(s, 0))
            yield c
            yield CounterMetricFamily("portal_pages_charged", "Pages charged for documents",
                                      value=db.scalar(select(func.coalesce(func.sum(Job.pages_charged), 0))) or 0)
            beats = GaugeMetricFamily("portal_beat_age_seconds", "Seconds since the portal worker's last pass (and the "
                                      "last backup)", labels=["name"])
            for b in db.scalars(select(WorkerBeat)):
                beats.add_metric([b.name], (now - b.at).total_seconds())
            yield beats
            yield GaugeMetricFamily("portal_alerts_open", "Problems super admins are being told about",
                                    value=db.scalar(select(func.count()).select_from(AlertState)) or 0)
            yield GaugeMetricFamily("portal_email_queued", "Emails waiting to be sent",
                                    value=db.scalar(select(func.count()).select_from(MailOutbox)
                                                    .where(MailOutbox.status == "queued")) or 0)


_collector: _Database | None = None


def render(sessions) -> bytes:
    """The metrics in Prometheus's text format. The database collector is added once, with the app's sessions."""
    global _collector
    if _collector is None:
        _collector = _Database(sessions)
        REGISTRY.register(_collector)
    return generate_latest(REGISTRY)


def now() -> float:
    return time.perf_counter()


# --- the portal worker's own (#397): served on a port of its own, as it answers no requests -----------------------

WORKER_REGISTRY = CollectorRegistry()
WORKER_BUSY = Gauge("portal_worker_busy", "1 while the worker is in a pass (documents, saves, emails)",
                    registry=WORKER_REGISTRY)
WORKER_PASSES = Counter("portal_worker_passes", "Passes the worker has completed", ["outcome"],
                        registry=WORKER_REGISTRY)
WORKER_PASS_SECONDS = Histogram("portal_worker_pass_seconds", "How long a pass takes",
                                buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120), registry=WORKER_REGISTRY)


def serve_worker(port: int) -> None:
    """Start the worker's metrics server (a thread); a port that can't be opened is logged, never fatal."""
    import logging

    from prometheus_client import start_http_server
    try:
        start_http_server(port, registry=WORKER_REGISTRY)
    except OSError as exc:
        logging.getLogger(__name__).warning("worker metrics not served on port %s: %s", port, exc)

