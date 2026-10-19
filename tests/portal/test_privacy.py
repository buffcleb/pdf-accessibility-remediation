"""#338: a person's data on request. Exported (records, or records and files) for a records request; removed, keeping
the service's records without them: their email and name gone from every record, their files deleted, what only
served them deleted, and the counts that reports and billing rely on unchanged."""
from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from portal.models import AuditEvent, AuthSession, HelpMessage, Job, QuotaLedger, User
from portal.storage import get_storage
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

EMAIL = "user@a.test"


def _person(s) -> User:
    return s["db"].scalar(select(User).where(User.email == EMAIL))


@pytest.fixture
def lived(setup):
    """The person has a finished document, a help request about it with an attachment, and a history."""
    s = setup
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("Smith family budget.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/help", data={"body": "Please check my file, user@a.test", "kind": "other"},
                            files=[("files", ("note.txt", b"my notes", "text/plain"))])
    assert r.status_code == 201, r.text
    s["job_id"] = job_id
    return s


def test_a_persons_data_is_exported_as_records_or_with_files(lived):
    s = lived
    pid = _person(s).id
    assert s["c"]["admin_a"].get(f"{API}/admin/users/{pid}/export").status_code == 403
    r = s["c"]["super"].get(f"{API}/admin/users/{pid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["person"]["email"] == EMAIL
    assert [d["filename"] for d in data["documents"]] == ["Smith family budget.pdf"]
    assert data["help_requests"][0]["messages"][0]["body"].startswith("Please check my file")
    assert any(e["action"] == "job.submitted" for e in data["audit"]) and data["page_ledger"]
    r = s["c"]["super"].get(f"{API}/admin/users/{pid}/export", params={"files": "true"})
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "person.json" in names and any(n.startswith("documents/") and n.endswith(".pdf") for n in names)
    assert s["db"].scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "person.exported")) == 2


def test_a_removed_person_is_gone_from_every_record_and_the_records_stay(lived):
    s, db = lived, lived["db"]
    person = _person(s)
    pid, job_id = person.id, uuid.UUID(s["job_id"])
    jobs_before = db.scalar(select(func.count()).select_from(Job))
    pages_before = db.scalar(select(func.sum(QuotaLedger.pages)).where(QuotaLedger.user_id == pid))
    keys = [f.s3_key for f in db.get(Job, job_id).files]
    sup = s["c"]["super"]
    assert s["c"]["admin_a"].post(f"{API}/admin/users/{pid}/remove",
                                  json={"confirm_email": EMAIL, "reason": "asked us"}).status_code == 403
    r = sup.post(f"{API}/admin/users/{pid}/remove", json={"confirm_email": "someone@else.test", "reason": "asked us"})
    assert r.status_code == 422  # the email must be typed
    r = sup.post(f"{API}/admin/users/{pid}/remove", json={"confirm_email": EMAIL, "reason": "Their request, ticket 77"})
    assert r.status_code == 200, r.text
    short = pid.hex[:8]
    assert r.json()["email"] == f"removed-{short}@removed.invalid" and r.json()["removed_at"]
    db.expire_all()
    # Nowhere in the database is their email any more
    for table in ("users", "audit_events", "quota_ledger", "jobs", "help_messages", "help_requests", "sessions",
                  "otp_challenges", "mail_outbox", "document_versions", "group_emails", "finding_marks"):
        hits = db.execute(text(f"select count(*) from {table} t where lower(t::text) like :e"),
                          {"e": f"%{EMAIL}%"}).scalar()
        assert hits == 0, table
    # Their files are gone, their document's name too; the records stay and still add up
    assert all(k not in get_storage().objects for k in keys)
    job = db.get(Job, job_id)
    assert job.files_deleted_at is not None and job.filename == f"document-{job_id.hex[:8]}.pdf"
    assert db.scalar(select(func.count()).select_from(Job)) == jobs_before
    assert db.scalar(select(func.sum(QuotaLedger.pages)).where(QuotaLedger.user_id == pid)) == pages_before
    assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == pid)) == 0
    assert all(m.body == "(removed with this person's data)" for m in db.scalars(
        select(HelpMessage).where(HelpMessage.author_id == pid)))
    assert s["c"]["user"].get(f"{API}/me").status_code == 401  # signed out everywhere
    reason = db.scalar(select(AuditEvent).where(AuditEvent.action == "person.removal_reason"))
    assert reason.details["reason"] == "Their request, ticket 77" and reason.actor_email == "root@admin.test"
    # Once is enough; and nobody removes themselves
    assert sup.post(f"{API}/admin/users/{pid}/remove",
                    json={"confirm_email": f"removed-{short}@removed.invalid", "reason": "again"}).status_code == 409
    root = db.scalar(select(User).where(User.email == "root@admin.test"))
    assert sup.post(f"{API}/admin/users/{root.id}/remove",
                    json={"confirm_email": "root@admin.test", "reason": "my own"}).status_code == 409


def test_the_audit_log_and_ledger_stay_append_only_outside_a_removal(lived):
    db = lived["db"]
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("update audit_events set action = 'changed'"))
    db.rollback()
    # Even during one, only who and from where may change, never what happened
    db.execute(text("select set_config('portal.anonymizing', 'on', true)"))
    db.execute(text("update audit_events set actor_email = 'x@removed.invalid'"))
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("update audit_events set action = 'changed'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("select set_config('portal.anonymizing', 'on', true)"))
        db.execute(text("update quota_ledger set pages = pages + 1"))
    db.rollback()
    assert json.dumps("ok")
