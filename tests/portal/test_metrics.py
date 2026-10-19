"""#397: the portal's metrics for Prometheus, at /metrics outside /api/: requests by route (never by path), and
documents, pages, the worker's beat and alerts read from the database."""
from __future__ import annotations

import uuid

from portal import metrics
from portal.models import Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def _value(text: str, name: str, **labels) -> float | None:
    want = name + ("{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else "")
    for line in text.splitlines():
        if line.startswith(want + " "):
            return float(line.split()[-1])
    return None


def test_the_portal_reports_documents_requests_and_its_worker(setup):
    s = setup
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("a.pdf", pdf_bytes(2)), ("b.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    s["c"]["user"].get(f"{API}/jobs/{job_id}")
    r = s["c"]["user"].get("/metrics", headers={"Authorization": ""})  # no sign-in needed: only Prometheus reaches it
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert _value(text, "portal_documents_finished_total", result="completed") == 1
    assert sum(_value(text, "portal_documents", status=st) for st in ("held", "queued", "submitted", "processing")) == 1
    assert _value(text, "portal_queue_oldest_seconds") > 0
    assert _value(text, "portal_pages_charged_total") == 3
    # Requests by their route's pattern: the document's ID never becomes a label
    assert _value(text, "portal_http_requests_total", method="GET", route="/api/v1/jobs/{job_id}", status="200") >= 1
    assert job_id not in text


def test_the_worker_counts_its_passes():
    before = metrics.WORKER_PASSES.labels("ok")._value.get()
    metrics.WORKER_BUSY.set(1)
    metrics.WORKER_PASSES.labels("ok").inc()
    metrics.WORKER_BUSY.set(0)
    from prometheus_client import generate_latest
    text = generate_latest(metrics.WORKER_REGISTRY).decode()
    assert _value(text, "portal_worker_busy") == 0
    assert _value(text, "portal_worker_passes_total", outcome="ok") == before + 1
