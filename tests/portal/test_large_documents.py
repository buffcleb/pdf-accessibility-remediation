"""Large documents are mixed in between smaller ones so the queue never jams, and go one after another when
nothing smaller is waiting. Super admins can process one now."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from portal import worker
from portal.config import get_portal_settings
from portal.models import AuditEvent, Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, setup, topup, upload  # noqa: F401

Q = f"{API}/admin/queues/service:standard"


@pytest.fixture
def large(setup, monkeypatch):
    s = setup
    topup(s, s["a"].id, 500)
    r = s["c"]["super"].patch(f"{API}/admin/settings", json={"large_doc_pages": 3, "large_doc_every": 2,
                                                              "large_doc_at_once": 1})
    assert r.status_code == 200
    return s


def _send(s, name, pages, who="user"):
    return upload(s["c"][who], (name, pdf_bytes(pages))).json()[0]["job"]["id"]


def _tick(s, finish=True):
    """One worker pass; then the engine finishes everything it was given."""
    db = s["db"]
    worker.submit_queued(db)
    if finish:
        for j in db.scalars(select(Job).where(Job.status.in_(("submitted", "processing")))):
            s["engine"].complete(j.engine_job_id)
        worker.poll_active(db)


def _started(s):
    return [name for name, _ in s["engine"].submitted]


def test_one_large_after_every_few_smaller_ones(large, monkeypatch):
    s = large
    monkeypatch.setattr(get_portal_settings(), "engine_max_in_flight", 1)
    for name, pages in [("L1", 5), ("L2", 5), ("S1", 1), ("S2", 1), ("S3", 1), ("S4", 1)]:
        _send(s, name, pages)
    _tick(s)
    _tick(s)
    held = s["db"].scalars(select(Job).where(Job.filename == "L2")).one()
    s["db"].refresh(held)
    assert held.status == "queued" and held.progress["stage"] == "waiting_large"
    for _ in range(4):
        _tick(s)
    assert _started(s) == ["L1", "S1", "S2", "L2", "S3", "S4"]


def test_with_nothing_smaller_waiting_large_ones_go_one_at_a_time(large):
    s = large
    for name in ("L1", "L2", "L3"):
        _send(s, name, 5)
    _tick(s, finish=False)
    assert _started(s) == ["L1"]  # four engine slots, but one large document at a time
    item = next(i for i in s["c"]["super"].get(f"{Q}/items?state=waiting").json()["items"] if i["title"] == "L2")
    assert item["detail"].startswith("Large document") and item["can_process_now"]
    job = s["c"]["user"].get(f"{API}/jobs/{item['id']}").json()
    assert job["progress"]["stage"] == "waiting_large"
    # A smaller one arriving goes straight past them
    _send(s, "S1", 1)
    _tick(s, finish=False)
    assert _started(s) == ["L1", "S1"]


def test_process_now_skips_the_large_document_rules(large):
    s = large
    for name in ("L1", "L2"):
        _send(s, name, 5)
    _tick(s, finish=False)
    l2 = s["db"].scalars(select(Job).where(Job.filename == "L2")).one().id
    assert s["c"]["admin_a"].post(f"{Q}/items/{l2}/process-now").status_code == 403
    assert s["c"]["super"].post(f"{Q}/items/{l2}/process-now").status_code == 204
    item = next(i for i in s["c"]["super"].get(f"{Q}/items?state=waiting").json()["items"] if i["id"] == str(l2))
    assert item["detail"].startswith("Process now") and not item["can_process_now"]
    _tick(s, finish=False)
    assert _started(s) == ["L1", "L2"]
    assert s["db"].scalar(select(AuditEvent).where(AuditEvent.action == "job.expedited")).target_id == str(l2)


def test_threshold_zero_turns_it_off(large):
    s = large
    s["c"]["super"].patch(f"{API}/admin/settings", json={"large_doc_pages": 0})
    for name in ("L1", "L2", "L3"):
        _send(s, name, 5)
    _tick(s, finish=False)
    assert _started(s) == ["L1", "L2", "L3"]
    assert all(i["can_process_now"] is False for i in s["c"]["super"].get(f"{Q}/items").json()["items"])


def test_settings_are_checked(large):
    sup = large["c"]["super"]
    assert sup.patch(f"{API}/admin/settings", json={"large_doc_every": 0}).status_code == 422
    assert sup.patch(f"{API}/admin/settings", json={"large_doc_at_once": 0}).status_code == 422
    assert sup.patch(f"{API}/admin/settings", json={"large_doc_pages": -1}).status_code == 422
