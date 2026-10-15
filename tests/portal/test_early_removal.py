"""#75: people mark a document done and its files go after a short hold; super admins remove files now or delete
documents (gone from the document lists, still in reports)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from portal import worker
from portal.models import AuditEvent, Job
from portal.storage import get_storage
from tests.portal.test_help import _ask, _completed
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def _backdate_request(db, job_id, hours):
    db.execute(update(Job).where(Job.id == uuid.UUID(job_id))
               .values(removal_requested_at=datetime.now(timezone.utc) - timedelta(hours=hours)))
    db.commit()


def _actions(db, job_id):
    return [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == job_id)
                                         .order_by(AuditEvent.id))]


def test_marked_done_files_go_after_the_hold(setup):
    s = setup
    job_id = _completed(s)
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/remove-early")
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["removal_requested"] and job["removal_hold_hours"] == 24
    removal_at = datetime.fromisoformat(job["removal_at"])
    assert timedelta(hours=23) < removal_at - datetime.now(timezone.utc) <= timedelta(hours=24)
    assert job["files_expire_at"] == job["removal_at"]  # the earlier of retention and the hold
    # During the hold nothing goes
    assert worker.sweep(s["db"])["files_jobs_marked_done"] == 0 and get_storage().objects
    _backdate_request(s["db"], job_id, 25)
    counts = worker.sweep(s["db"])
    assert counts["files_jobs_marked_done"] == 1 and not get_storage().objects
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["files_deleted"] and job["files"] == [] and job["removal_at"] is None
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] == 2  # usage survives
    assert _actions(s["db"], job_id)[-2:] == ["job.removal_requested", "job.files_removed"]


def test_keep_it_undoes_the_request(setup):
    s = setup
    job_id = _completed(s)
    s["c"]["user"].post(f"{API}/jobs/{job_id}/remove-early")
    assert s["c"]["user"].post(f"{API}/jobs/{job_id}/remove-early").json()["detail"]["error"] == "already"
    job = s["c"]["user"].post(f"{API}/jobs/{job_id}/keep").json()
    assert not job["removal_requested"] and job["removal_at"] is None
    assert worker.sweep(s["db"])["files_jobs_marked_done"] == 0 and get_storage().objects
    assert s["c"]["user"].post(f"{API}/jobs/{job_id}/keep").json()["detail"]["error"] == "not_marked"
    assert "job.removal_canceled" in _actions(s["db"], job_id)


def test_hold_of_zero_removes_at_the_next_sweep(setup):
    s = setup
    s["c"]["super"].patch(f"{API}/admin/settings", json={"early_removal_hold_hours": 0})
    job_id = _completed(s)
    s["c"]["user"].post(f"{API}/jobs/{job_id}/remove-early")
    assert worker.sweep(s["db"])["files_jobs_marked_done"] == 1


def test_refusals(setup):
    s = setup
    topup(s, s["a"].id, 100)
    waiting = upload(s["c"]["user"], ("w.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    r = s["c"]["user"].post(f"{API}/jobs/{waiting}/remove-early")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_finished"
    done = _completed(s)
    assert s["c"]["user2"].post(f"{API}/jobs/{done}/remove-early").status_code == 404  # someone else's
    assert s["c"]["admin_a"].post(f"{API}/jobs/{done}/remove-early").status_code == 404  # only the owner
    _ask(s, done)
    r = s["c"]["user"].post(f"{API}/jobs/{done}/remove-early")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "help_open"


def test_bulk_marks_what_it_can(setup):
    s = setup
    a, b = _completed(s), _completed(s)
    waiting = upload(s["c"]["user"], ("w.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    other = _completed(s, client="user2")
    r = s["c"]["user"].post(f"{API}/jobs/remove-early", json={"ids": [a, b, waiting, other]}).json()
    assert sorted(r["done"]) == sorted([a, b])
    assert {x["id"]: x["reason"] for x in r["skipped"]} == {waiting: "not_finished", other: "not_found"}


def test_super_admin_removes_files_now(setup):
    s = setup
    job_id = _completed(s)
    body = {"ids": [job_id], "reason": "test documents"}
    assert s["c"]["admin_a"].post(f"{API}/admin/jobs/remove", json=body).status_code == 403
    r = s["c"]["super"].post(f"{API}/admin/jobs/remove", json=body).json()
    assert r["done"] == [job_id] and not get_storage().objects
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["files_deleted"] and not job["deleted"]  # still listed for its owner
    assert job_id in [j["id"] for j in s["c"]["user"].get(f"{API}/documents").json()["items"]]
    ev = s["db"].scalars(select(AuditEvent).where(AuditEvent.action == "job.files_removed")).one()
    assert ev.details["reason"] == "test documents" and ev.details["owner"] == "user@a.test"
    again = s["c"]["super"].post(f"{API}/admin/jobs/remove", json=body).json()
    assert again["skipped"][0]["reason"] == "files_gone"


def test_super_admin_delete_hides_the_document_but_keeps_reports(setup):
    s = setup
    a = s["a"].id
    job_id, kept = _completed(s), _completed(s)
    waiting = upload(s["c"]["user"], ("w.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    r = s["c"]["super"].post(f"{API}/admin/jobs/remove",
                             json={"ids": [job_id, waiting], "delete": True, "reason": "cleanup"}).json()
    assert r["done"] == [job_id] and r["skipped"][0]["reason"] == "not_finished"
    # Gone from the owner's lists and the group's, and from the owner's and group admin's view
    mine = [j["id"] for j in s["c"]["user"].get(f"{API}/documents").json()["items"]]
    assert job_id not in mine and kept in mine
    assert job_id not in [j["id"] for j in s["c"]["user"].get(f"{API}/jobs").json()]
    assert job_id not in [j["id"] for j in s["c"]["admin_a"].get(f"{API}/groups/{a}/jobs").json()]
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}").status_code == 404
    assert s["c"]["admin_a"].get(f"{API}/jobs/{job_id}").status_code == 404
    # Super admins still see it, marked deleted; usage and reports still count it
    seen = s["c"]["super"].get(f"{API}/jobs/{job_id}").json()
    assert seen["deleted"] and seen["deleted_by"] == "root@admin.test" and seen["files_deleted"]
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] >= 4
    report = s["c"]["super"].get(f"{API}/reports/documents").json()
    assert job_id in str(report)
    assert "job.deleted" in _actions(s["db"], job_id)
