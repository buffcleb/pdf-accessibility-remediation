"""#69: every email is logged (sign-in codes and test emails too, never their body), and shown as a report."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from portal import outbox, worker
from portal.mail import MailError, get_mailer
from portal.models import Job, MailOutbox
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def _rows(db, **where):
    stmt = select(MailOutbox).order_by(MailOutbox.created_at)
    for k, v in where.items():
        stmt = stmt.where(getattr(MailOutbox, k) == v)
    return db.scalars(stmt).all()


def test_sign_in_codes_are_logged_without_the_code(setup, make_client):
    s = setup
    make_client().post(f"{API}/auth/code", json={"email": "user@a.test"})
    row = _rows(s["db"], kind="sign_in", to_addr="user@a.test")[-1]
    assert row.status == "sent" and row.backend == "memory" and row.body == ""
    code_mail = get_mailer().outbox[-1]
    assert row.message_id == code_mail.message_id and row.subject == code_mail.subject
    assert not any(ch.isdigit() for ch in row.subject)  # the code is never in a subject


def test_a_failed_sign_in_code_is_logged_with_its_error(setup, make_client, monkeypatch):
    from portal.mail import Mailer

    def broken(self, mail, from_name=None, attempts=None):
        raise MailError("relay down", retryable=True)

    monkeypatch.setattr(Mailer, "deliver", broken)
    make_client().post(f"{API}/auth/code", json={"email": "user2@a.test"})
    row = _rows(setup["db"], kind="sign_in", to_addr="user2@a.test")[-1]
    assert row.status == "failed" and row.last_error == "relay down" and row.message_id is None


def test_outbox_mail_records_backend_and_message_id(setup):
    s = setup
    topup(s, s["a"].id, 10)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    row = _rows(s["db"], kind="job", to_addr="user@a.test")[-1]
    assert row.status == "sent" and row.backend == "memory" and row.message_id


def test_the_test_email_is_logged(setup):
    setup["c"]["super"].post(f"{API}/admin/mail/test", json={})
    assert _rows(setup["db"], kind="test")[-1].status == "sent"


def test_the_report_shows_it_to_super_admins_and_each_group_its_own(setup, make_client):
    s = setup
    make_client().post(f"{API}/auth/code", json={"email": "user@a.test"})
    body = s["c"]["super"].get(f"{API}/reports/email").json()
    assert {"to", "kind", "status", "message_id"} <= {c["key"] for c in body["columns"]}
    assert any(r["kind"] == "Sign-in code" and r["to"] == "user@a.test" for r in body["rows"])
    assert body["headline"][0]["label"] == "Sent" and body["headline"][0]["value"] >= 1
    found = s["c"]["super"].get(f"{API}/reports/email", params={"q": "user@a.test", "kind": "sign_in"}).json()
    assert found["rows"] and all(r["to"] == "user@a.test" for r in found["rows"])
    # Group B's admin sees only group B's people
    rows_b = s["c"]["admin_b"].get(f"{API}/reports/email").json()["rows"]
    assert rows_b and all(r["to"].endswith("@b.test") for r in rows_b)


def test_bodies_go_after_30_days_records_after_the_records_period(setup):
    s, db = setup, setup["db"]
    s["c"]["super"].patch(f"{API}/admin/settings", json={"record_retention_days": 60})
    outbox.queue(db, [__import__("portal.mail", fromlist=["Mail"]).Mail("x@a.test", "Hi", "Body text")], None, "job")
    db.commit()
    outbox.send_queued(db)
    old = datetime.now(timezone.utc) - timedelta(days=31)
    db.execute(update(MailOutbox).where(MailOutbox.to_addr == "x@a.test").values(created_at=old))
    db.commit()
    worker.sweep(db)
    row = _rows(db, to_addr="x@a.test")[-1]
    assert row.body == "" and row.subject == "Hi" and row.status == "sent"   # kept, without what it said
    db.execute(update(MailOutbox).where(MailOutbox.to_addr == "x@a.test")
               .values(created_at=datetime.now(timezone.utc) - timedelta(days=61)))
    db.commit()
    worker.sweep(db)
    assert not _rows(db, to_addr="x@a.test")
