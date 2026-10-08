"""Queues: see them, pause and resume them, retry and cancel single items. Sign-in codes are never queued."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal import outbox, worker
from portal.mail import MailError, get_mailer
from portal.models import AuditEvent, Job, MailOutbox
from portal.queues import job_services, paused_services
from tests.portal.conftest import last_code
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

Q = f"{API}/admin/queues"
STD = "service:standard"


def _queue(s, key):
    return next(q for q in s["c"]["super"].get(Q).json() if q["key"] == key)


def test_only_super_admins_see_the_queues(setup):
    s = setup
    assert s["c"]["admin_a"].get(Q).status_code == 403
    assert s["c"]["admin_a"].post(f"{Q}/mail/pause").status_code == 403
    keys = [q["key"] for q in s["c"]["super"].get(Q).json()]
    assert keys == [STD, "edits", "reviews", "mail"]
    assert s["c"]["super"].get(f"{Q}/nope/items").status_code == 404


def test_pausing_a_service_holds_new_documents_and_lets_started_ones_finish(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    first = upload(s["c"]["user"], ("one.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(db)  # sent to the engine
    r = s["c"]["super"].post(f"{Q}/{STD}/pause", json={"note": "Engine upgrade"})
    assert r.status_code == 200
    q = next(x for x in r.json() if x["key"] == STD)
    assert q["paused"]["by"] == "root@admin.test" and q["paused"]["note"] == "Engine upgrade"

    second = upload(s["c"]["user2"], ("two.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    s["engine"].complete(db.get(Job, uuid.UUID(first)).engine_job_id)
    run_worker(db)
    assert s["c"]["user"].get(f"{API}/jobs/{first}").json()["status"] == "completed"  # started before the pause
    held = s["c"]["user2"].get(f"{API}/jobs/{second}").json()
    assert held["status"] == "queued" and held["progress"]["stage"] == "paused"
    assert len(s["engine"].submitted) == 1

    q = _queue(s, STD)
    assert q["waiting"] == 1 and q["active"] == 0
    item = s["c"]["super"].get(f"{Q}/{STD}/items?state=waiting").json()["items"][0]
    assert item["title"] == "two.pdf" and item["detail"] == "Paused" and item["can_cancel"]
    # Group admins are told why, without the details of the queue
    att = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/attention").json()
    assert any(i["kind"] == "queue_paused" for i in att)

    assert s["c"]["super"].post(f"{Q}/{STD}/resume").status_code == 200
    run_worker(db)
    assert s["c"]["user2"].get(f"{API}/jobs/{second}").json()["status"] in ("submitted", "processing")
    actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_type == "queue"))]
    assert sorted(actions) == ["queue.paused", "queue.resumed"]


def test_a_paused_ai_option_holds_documents_that_use_it():
    job = Job(processing="standard", ai_tier="ai:cloud", report_only=False)
    assert job_services(job) == ["standard", "ai:cloud"]
    assert paused_services(job, {"service:ai:cloud": object()}) == ["ai:cloud"]
    # In the Standard queue it says what holds it; in the AI option's own queue, just that it's paused
    from portal.queues import _wait_reason
    labels = {"standard": "Standard", "ai:cloud": "Cloud AI"}
    pauses = {"service:ai:cloud": object()}
    assert _wait_reason(job, pauses, labels, "standard") == "Waiting for Cloud AI, which is paused"
    assert _wait_reason(job, pauses, labels, "ai:cloud") == "Paused"
    report = Job(processing="adobe", ai_tier="ai:cloud", report_only=True)
    assert paused_services(report, {"service:ai:cloud": object(), "service:adobe": object()}) == []


def test_support_can_cancel_a_waiting_document_and_the_person_is_told(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    s["c"]["super"].post(f"{Q}/{STD}/pause")
    job_id = upload(s["c"]["user"], ("wait.pdf", pdf_bytes(4))).json()[0]["job"]["id"]
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 4
    assert s["c"]["super"].post(f"{Q}/{STD}/items/{job_id}/cancel").status_code == 204
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["status"] == "canceled"
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 0
    run_worker(db)
    assert get_mailer().outbox[-1].subject == "Canceled: wait.pdf"
    assert s["c"]["super"].post(f"{Q}/{STD}/items/{job_id}/cancel").status_code == 409


def test_a_failed_document_can_be_tried_again_on_the_persons_limits(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("flaky.pdf", pdf_bytes(3))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].fail(db.get(Job, uuid.UUID(job_id)).engine_job_id, "engine_lost")
    run_worker(db)
    item = s["c"]["super"].get(f"{Q}/{STD}/items?state=failed").json()["items"][0]
    assert item["id"] == job_id and item["can_retry"] and item["error_code"] == "engine_lost"
    assert s["c"]["super"].post(f"{Q}/{STD}/items/{job_id}/retry").status_code == 204
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "queued" and job["pages_charged"] == 3 and not job.get("error_code")
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 3
    run_worker(db)
    assert len(s["engine"].submitted) == 2
    assert s["c"]["super"].post(f"{Q}/{STD}/items/{job_id}/retry").status_code == 409  # not failed now


def test_email_waits_while_paused_but_sign_in_codes_never_do(setup, make_client):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    s["c"]["super"].post(f"{Q}/mail/pause")
    job_id = upload(s["c"]["user"], ("doc.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    assert get_mailer().outbox == []
    assert _queue(s, "mail")["waiting"] == 1
    item = s["c"]["super"].get(f"{Q}/mail/items").json()["items"][0]
    assert item["title"] == "Done: doc.pdf" and item["who"] == "user@a.test" and item["detail"] == "Paused"

    # Signing in still works: the code isn't queued
    r = make_client().post(f"{API}/auth/code", json={"email": "user2@a.test"})
    assert r.status_code == 202 and last_code("user2@a.test")

    s["c"]["super"].post(f"{Q}/mail/resume")
    outbox.send_queued(db)
    assert [m.subject for m in get_mailer().outbox if m.to == "user@a.test"] == ["Done: doc.pdf"]


def test_email_retries_temporary_failures_then_waits_for_support(setup, monkeypatch):
    s, db = setup, setup["db"]
    from portal.mail import Mail
    outbox.queue(db, [Mail(to="user@a.test", subject="Hello", text="Hi")], None, "test")
    db.commit()
    mailer = get_mailer()

    def down(*a, **k):
        raise MailError("provider answered HTTP 503", retryable=True)
    monkeypatch.setattr(mailer, "deliver", down)
    for _ in range(3):
        outbox.send_queued(db)
        db.execute(MailOutbox.__table__.update().values(next_try_at=MailOutbox.created_at))  # skip the wait
        db.commit()
    m = db.scalars(select(MailOutbox)).one()
    db.refresh(m)
    assert m.status == "failed" and m.attempts == 3 and "503" in m.last_error
    att = s["c"]["super"].get(f"{API}/admin/attention").json()
    assert any(i["kind"] == "mail_failed" for i in (att["items"] if isinstance(att, dict) else att))

    monkeypatch.undo()
    assert s["c"]["super"].post(f"{Q}/mail/items/{m.id}/retry").status_code == 204
    assert outbox.send_queued(db) == 1
    assert get_mailer().outbox[-1].subject == "Hello"


def test_queued_email_can_be_canceled(setup):
    s, db = setup, setup["db"]
    from portal.mail import Mail
    s["c"]["super"].post(f"{Q}/mail/pause")
    outbox.queue(db, [Mail(to="user@a.test", subject="Never mind", text="x")], None, "test")
    db.commit()
    mid = s["c"]["super"].get(f"{Q}/mail/items").json()["items"][0]["id"]
    assert s["c"]["super"].post(f"{Q}/mail/items/{mid}/cancel").status_code == 204
    s["c"]["super"].post(f"{Q}/mail/resume")
    assert outbox.send_queued(db) == 0


def test_paused_saved_edits_wait(setup):
    s, db = setup, setup["db"]
    s["c"]["super"].post(f"{Q}/edits/pause")
    assert worker.submit_versions(db) == 0
    assert _queue(s, "edits")["paused"]["by"] == "root@admin.test"
