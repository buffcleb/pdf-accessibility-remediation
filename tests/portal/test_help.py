"""#11 part A: asking for help with a document, the conversation, statuses and the queue."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal import helpdesk, worker
from portal.mail import get_mailer
from portal.models import DocumentVersion, HelpAttachment, HelpRequest, Job
from portal.storage import get_storage
from tests.portal.conftest import sent_mail
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _completed(s, client="user"):
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"][client], ("doc.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    return job_id


def _ask(s, job_id, client="user", files=None, **data):
    form = {"body": "The chart on page 1 is read out as 'image'.", "kind": "alt_text", **data}
    return s["c"][client].post(f"{API}/jobs/{job_id}/help", data=form, files=files or [])


def _mails(to):
    return [m for m in sent_mail() if m.to == to]


def test_asking_opens_one_request_and_tells_super_admins(setup):
    s = setup
    job_id = _completed(s)
    r = _ask(s, job_id, version="1", page="1", files=[("files", ("notes.txt", b"what the reader said", "text/plain"))])
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "open" and req["role"] == "person" and req["version_label"]
    assert req["messages"][0]["attachments"][0]["name"] == "notes.txt"
    assert "doc.pdf" in _mails("root@admin.test")[-1].subject
    assert s["c"]["super"].get(f"{API}/me").json()["help_needs_us"] == 1

    again = _ask(s, job_id)
    assert again.status_code == 409 and again.json()["detail"]["error"] == "help_open"
    assert _ask(s, job_id, client="user2").status_code == 404  # not their document


def test_only_finished_documents_and_known_kinds(setup):
    s = setup
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("doc.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    assert _ask(s, job_id).json()["detail"]["error"] == "not_ready"
    job_id = _completed(s)
    assert _ask(s, job_id, kind="spelling").status_code == 422


def test_attachments_are_checked_by_content_and_size(setup):
    s = setup
    job_id = _completed(s)
    bad = _ask(s, job_id, files=[("files", ("report.pdf", b"MZ\x90\x00 not a pdf", "application/pdf"))])
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "file_type"
    s["c"]["super"].patch(f"{API}/admin/settings", json={"help_max_file_mb": 1})
    big = _ask(s, job_id, files=[("files", ("big.pdf", b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10), "application/pdf"))])
    assert big.status_code == 422 and big.json()["detail"]["error"] == "file_too_large"
    ok = _ask(s, job_id, files=[("files", ("shot.png", PNG, "image/png"))])
    assert ok.status_code == 201
    att = ok.json()["messages"][0]["attachments"][0]
    assert att["content_type"] == "image/png"
    path = f"{API}/help/{ok.json()['id']}/attachments/{att['id']}"
    assert s["c"]["user"].get(path).content == PNG
    assert s["c"]["admin_a"].get(path).status_code == 200
    assert s["c"]["admin_b"].get(path).status_code == 404


def test_conversation_internal_notes_and_who_is_told(setup):
    s = setup
    req = _ask(s, _completed(s)).json()
    base = f"{API}/help/{req['id']}"
    get_mailer().outbox.clear()

    r = s["c"]["super"].post(f"{base}/messages", data={"body": "Is the source data available?"})
    assert r.status_code == 201 and r.json()["role"] == "super_admin"
    assert [m["body"] for m in r.json()["messages"]].count("Is the source data available?") == 1
    mail = _mails("user@a.test")[-1]  # the person hears about it, once
    assert mail.text.count("Is the source data available?") == 1
    note = s["c"]["super"].post(f"{base}/messages", data={"body": "AI missed the data chart.", "internal": "true"})
    assert note.status_code == 201
    assert s["c"]["user"].post(f"{base}/messages", data={"body": "x", "internal": "true"}).status_code == 403

    for client in ("user", "admin_a"):
        kinds = [m["kind"] for m in s["c"][client].get(base).json()["messages"]]
        assert "note" not in kinds, client
    assert "note" in [m["kind"] for m in s["c"]["super"].get(base).json()["messages"]]
    assert s["c"]["admin_b"].get(base).status_code == 404
    assert s["c"]["user2"].get(base).status_code == 404

    get_mailer().outbox.clear()
    r = s["c"]["admin_a"].post(f"{base}/messages", data={"body": "I can send the spreadsheet."})
    assert r.status_code == 201 and r.json()["needs_staff"] is True
    # Both sides heard something minutes ago, so this one waits for the batch email to each side
    hr = s["db"].get(HelpRequest, uuid.UUID(req["id"]))
    s["db"].refresh(hr)
    assert hr.staff_notify_pending is True and hr.person_notify_pending is True


def test_statuses_belong_to_super_admins_and_a_reply_moves_it_back(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id).json()
    base = f"{API}/help/{req['id']}"
    assert s["c"]["admin_a"].post(f"{base}/status", json={"status": "resolved"}).status_code == 403
    assert s["c"]["user"].post(f"{base}/status", json={"status": "closed"}).status_code == 403

    r = s["c"]["super"].post(f"{base}/status", json={"status": "waiting"}).json()
    assert r["status"] == "waiting" and r["assignee_email"] == "root@admin.test"
    assert s["c"]["user"].get(f"{API}/me").json()["help_unread"] == 1
    events = [m["body"] for m in s["c"]["user"].get(base).json()["messages"] if m["kind"] == "event"]
    assert "Status: We need something from you" in events and not any("root@admin.test" in e for e in events)
    staff_events = [m["body"] for m in s["c"]["super"].get(base).json()["messages"] if m["kind"] == "event"]
    assert "Status: Waiting on the person (root@admin.test)" in staff_events
    r = s["c"]["user"].post(f"{base}/messages", data={"body": "Attached the data."}).json()
    assert r["status"] == "in_progress" and r["needs_staff"] is True and r["unread"] is False

    s["c"]["super"].post(f"{base}/status", json={"status": "resolved"})
    done = s["c"]["user"].post(f"{base}/messages", data={"body": "One more thing"})
    assert done.status_code == 409 and done.json()["detail"]["error"] == "help_finished"
    second = _ask(s, job_id)  # a new request is fine once the old one is finished
    assert second.status_code == 201
    reopen = s["c"]["super"].post(f"{base}/status", json={"status": "in_progress"})
    assert reopen.status_code == 409  # the document already has an active request


def test_taking_a_request_and_the_queues(setup):
    s = setup
    req = _ask(s, _completed(s)).json()
    r = s["c"]["super"].post(f"{API}/help/{req['id']}/assign", json={"take": True}).json()
    assert r["assignee_email"] == "root@admin.test" and r["status"] == "in_progress"

    queue = s["c"]["super"].get(f"{API}/admin/help").json()
    assert [q["id"] for q in queue] == [req["id"]] and queue[0]["processing"] == "standard"
    assert s["c"]["super"].get(f"{API}/admin/help", params={"saved": "true"}).json() == []
    assert s["c"]["admin_a"].get(f"{API}/admin/help").status_code == 403
    assert [q["id"] for q in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/help").json()] == [req["id"]]
    assert s["c"]["admin_b"].get(f"{API}/groups/{s['a'].id}/help").status_code == 404


def test_my_documents_show_the_request_and_unread_replies(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id).json()
    s["c"]["super"].post(f"{API}/help/{req['id']}/messages", data={"body": "Looking at it now."})
    job = next(j for j in s["c"]["user"].get(f"{API}/jobs").json() if j["id"] == job_id)
    assert job["help"] == {"id": req["id"], "status": "open", "unread": True}
    s["c"]["user"].get(f"{API}/jobs/{job_id}/help")  # reading it marks it seen
    job = next(j for j in s["c"]["user"].get(f"{API}/jobs").json() if j["id"] == job_id)
    assert job["help"]["unread"] is False


def test_quick_replies_go_out_as_one_email(setup):
    s = setup
    req = _ask(s, _completed(s)).json()
    base = f"{API}/help/{req['id']}"
    get_mailer().outbox.clear()
    s["c"]["super"].post(f"{base}/messages", data={"body": "First"})
    s["c"]["super"].post(f"{base}/messages", data={"body": "Second"})
    s["c"]["super"].post(f"{base}/messages", data={"body": "Third"})
    assert len(_mails("user@a.test")) == 1

    db = s["db"]
    hr = db.get(HelpRequest, uuid.UUID(req["id"]))
    db.refresh(hr)
    assert hr.person_notify_pending is True
    shift = helpdesk.NOTIFY_WINDOW + timedelta(seconds=1)  # as if the window had passed
    hr.person_notified_at -= shift
    for m in hr.messages:
        m.created_at -= shift
    db.commit()
    assert helpdesk.send_pending(db) == 1
    digest = _mails("user@a.test")[-1]
    assert "Second" in digest.text and "Third" in digest.text and "First" not in digest.text


def test_requests_expire_with_the_documents_files(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id, files=[("files", ("shot.png", PNG, "image/png"))]).json()
    db = s["db"]
    key = db.scalar(select(HelpAttachment.s3_key))
    assert key in get_storage().objects
    job = db.get(Job, uuid.UUID(job_id))
    job.created_at = datetime.now(timezone.utc) - timedelta(days=400)
    db.commit()
    counts = worker.sweep(db)
    assert counts["help_requests"] == 1 and db.get(HelpRequest, uuid.UUID(req["id"])) is None
    assert key not in get_storage().objects


def test_saved_requests_outlive_the_documents_files(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id).json()
    db = s["db"]
    hr = db.get(HelpRequest, uuid.UUID(req["id"]))
    hr.saved = True
    db.get(Job, uuid.UUID(job_id)).created_at = datetime.now(timezone.utc) - timedelta(days=400)
    db.commit()
    worker.sweep(db)
    db.expire_all()
    assert db.get(HelpRequest, uuid.UUID(req["id"])) is not None


def test_new_requests_are_rate_limited(setup, monkeypatch):
    from portal.routers import help as help_router
    monkeypatch.setattr(help_router, "NEW_PER_DAY", 1)
    s = setup
    first, second = _completed(s), _completed(s)
    assert _ask(s, first).status_code == 201
    assert _ask(s, second).status_code == 429


# --- part B: resolving, corrected files, reopening, saving, reminders -------------------------------

def _versions(s, job_id):
    return s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"]


def test_a_corrected_file_becomes_a_checked_version(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id, version="1").json()
    base = f"{API}/help/{req['id']}"
    wrong = s["c"]["super"].post(f"{base}/corrected", files={"file": ("fixed.pdf", pdf_bytes(3), "application/pdf")})
    assert wrong.status_code == 422 and wrong.json()["detail"]["error"] == "page_count"
    assert s["c"]["admin_a"].post(f"{base}/corrected", files={"file": ("f.pdf", pdf_bytes(2), "application/pdf")}).status_code == 403

    r = s["c"]["super"].post(f"{base}/corrected", files={"file": ("fixed.pdf", pdf_bytes(2), "application/pdf")})
    assert r.status_code == 201, r.text
    v = _versions(s, job_id)[-1]
    assert v["kind"] == "corrected" and v["label"] == "Corrected by support 1" and v["status"] == "queued"
    # The worker sends the uploaded file, with the version it replaces as the reference for the visual check
    worker.submit_versions(s["db"])
    assert s["engine"].submitted[-1][1:3] == ("edit", []) and s["engine"].references[-1] is not None
    corrected = s["db"].scalar(select(DocumentVersion).where(DocumentVersion.kind == "corrected"))
    s["engine"].complete_edit(corrected.engine_job_id)
    worker.poll_versions(s["db"])
    assert _versions(s, job_id)[-1]["status"] == "ready"
    # Support sees the upload in the thread; the person doesn't
    assert any("Corrected file uploaded" in m["body"] for m in s["c"]["super"].get(base).json()["messages"])
    assert not any("Corrected" in m["body"] for m in s["c"]["user"].get(base).json()["messages"])


def test_resolving_with_a_version_a_note_and_pages_back(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id).json()
    base = f"{API}/help/{req['id']}"
    used = s["c"]["user"].get(f"{API}/me").json()["used_pages"]
    assert s["c"]["super"].post(f"{base}/resolve", json={"outcome": "resolved"}).json()["detail"]["error"] == "note_required"
    too_many = s["c"]["super"].post(f"{base}/resolve", json={"note": "Fixed", "refund_pages": 99})
    assert too_many.json()["detail"]["error"] == "refund_too_large"
    assert s["c"]["admin_a"].post(f"{base}/resolve", json={"note": "x"}).status_code == 403

    get_mailer().outbox.clear()
    r = s["c"]["super"].post(f"{base}/resolve", json={"note": "Added a header row to the table.", "version_number": 1,
                                                      "refund_pages": 2})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "resolved" and out["resolution"] == "Added a header row to the table."
    assert out["resolved_version_number"] == 1 and out["pages_refunded"] == 2
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] == used - 2
    assert "Added a header row" in _mails("user@a.test")[-1].text
    assert s["c"]["super"].post(f"{base}/resolve", json={"note": "again"}).status_code == 409


def test_the_person_can_reopen_for_a_while(setup):
    s = setup
    job_id = _completed(s)
    req = _ask(s, job_id).json()
    base = f"{API}/help/{req['id']}"
    s["c"]["super"].post(f"{base}/resolve", json={"outcome": "closed"})
    mine = s["c"]["user"].get(base).json()
    assert mine["can_reopen"] is True and mine["reopen_until"]
    assert s["c"]["admin_a"].post(f"{base}/reopen").status_code == 409  # group admins can't
    r = s["c"]["user"].post(f"{base}/reopen")
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["needs_staff"] is True

    s["c"]["super"].post(f"{base}/resolve", json={"outcome": "closed"})
    hr = s["db"].get(HelpRequest, uuid.UUID(req["id"]))
    s["db"].refresh(hr)
    hr.finished_at -= timedelta(days=15)
    s["db"].commit()
    assert s["c"]["user"].get(base).json()["can_reopen"] is False
    assert s["c"]["user"].post(f"{base}/reopen").status_code == 409
    assert s["c"]["super"].post(f"{base}/reopen").status_code == 200  # super admins can, any time


def test_saving_moves_a_request_to_the_saved_view(setup):
    s = setup
    req = _ask(s, _completed(s)).json()
    base = f"{API}/help/{req['id']}"
    assert s["c"]["admin_a"].post(f"{base}/save", json={"saved": True}).status_code == 403
    assert s["c"]["super"].post(f"{base}/save", json={"saved": True}).json()["saved"] is True
    assert [x["id"] for x in s["c"]["super"].get(f"{API}/admin/help", params={"saved": "true"}).json()] == [req["id"]]
    assert s["c"]["super"].get(f"{API}/admin/help").json() == []


def test_reminders_at_3_and_7_days_then_closed_at_10(setup):
    s = setup
    req = _ask(s, _completed(s)).json()
    s["c"]["super"].post(f"{API}/help/{req['id']}/messages", data={"body": "Could you send the source file?"})
    s["c"]["super"].post(f"{API}/help/{req['id']}/status", json={"status": "waiting"})
    db = s["db"]
    hr = db.get(HelpRequest, uuid.UUID(req["id"]))

    def age(days):
        db.refresh(hr)
        hr.status_changed_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()
        get_mailer().outbox.clear()
        return helpdesk.remind_and_close(db)

    assert age(1) == {"reminded": 0, "closed": 0}
    assert age(3) == {"reminded": 1, "closed": 0} and "Could you send the source file?" in _mails("user@a.test")[-1].text
    assert age(4) == {"reminded": 0, "closed": 0}  # one reminder per step
    assert age(7)["reminded"] == 1
    assert age(10) == {"reminded": 0, "closed": 1}
    db.refresh(hr)
    assert hr.status == "closed" and "closed" in _mails("user@a.test")[-1].subject
    events = [m["body"] for m in s["c"]["user"].get(f"{API}/help/{req['id']}").json()["messages"] if m["kind"] == "event"]
    assert any("Closed after 10 days" in e for e in events)
    assert s["c"]["user"].get(f"{API}/help/{req['id']}").json()["can_reopen"] is True


# --- documents that fail (#141) -------------------------------------------------------------------

def _failed(s, code="tag_integrity_failed"):
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("doc.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].fail(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id, code)
    run_worker(s["db"])
    return job_id


def test_a_failed_document_gets_a_help_request_of_its_own(setup):
    s = setup
    job_id = _failed(s)
    req = s["c"]["user"].get(f"{API}/jobs/{job_id}/help").json()
    assert req["kind"] == "failed" and req["status"] == "open" and req["role"] == "person"
    first = req["messages"][0]
    assert first["author_email"] == "The service" and "tag_integrity_failed" in first["body"]
    # What the engine recorded is for super admins: a note on the request, and in their email
    assert len(req["messages"]) == 1 and "internal detail" not in first["body"]
    staff = s["c"]["super"].get(f"{API}/help/{req['id']}").json()
    assert [m["kind"] for m in staff["messages"]] == ["message", "note"] and "internal detail" in staff["messages"][1]["body"]
    mail = _mails("root@admin.test")[-1]
    assert mail.subject == "Failed document: doc.pdf" and "couldn't be processed" in mail.text and "internal detail" in mail.text
    assert s["c"]["super"].get(f"{API}/me").json()["help_needs_us"] == 1
    # The person adds to it rather than opening a second one
    assert _ask(s, job_id).status_code == 409
    more = s["c"]["user"].post(f"{API}/help/{req['id']}/messages", data={"body": "It's a scan of a signed form."})
    assert more.status_code == 201 and len(more.json()["messages"]) == 2


def test_with_the_setting_off_the_person_can_still_ask(setup):
    s = setup
    r = s["c"]["super"].patch(f"{API}/admin/settings", json={"help_on_failure": False})
    assert r.status_code == 200 and r.json()["help_on_failure"] is False
    job_id = _failed(s)
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/help").json() is None
    asked = _ask(s, job_id, kind="tables", version="1", page="2")   # the form's choices don't apply to a failure
    assert asked.status_code == 201, asked.text
    assert asked.json()["kind"] == "failed" and asked.json()["version_label"] is None and asked.json()["page"] is None


def test_a_document_someone_stopped_gets_no_request(setup):
    s = setup
    job_id = _failed(s, code="stopped")
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/help").json() is None


def test_the_failed_kind_is_not_one_to_choose(setup):
    s = setup
    assert _ask(s, _completed(s), kind="failed").status_code == 422
