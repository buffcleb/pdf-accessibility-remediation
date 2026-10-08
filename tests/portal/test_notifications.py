"""How often people get notification emails: as they happen, in one daily summary, or not at all."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal import notifications, outbox
from portal.mail import get_mailer
from portal.models import AuditEvent, Job, MailOutbox, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

N = f"{API}/me/notifications"


def _finish(s, name="doc.pdf", who="user"):
    job_id = upload(s["c"][who], (name, pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])


def _to(email):
    return [m for m in get_mailer().outbox if m.to == email]


def test_people_choose_and_admins_get_one_more_kind(setup):
    s = setup
    assert [c["category"] for c in s["c"]["user"].get(N).json()["choices"]] == ["documents", "help"]
    assert [(c["category"], c["choice"]) for c in s["c"]["admin_a"].get(N).json()["choices"]] == [
        ("documents", "each"), ("help", "each"), ("admin", "each")]
    assert s["c"]["user"].put(N, json={"choices": {"admin": "off"}}).status_code == 422  # not an admin
    assert s["c"]["user"].put(N, json={"choices": {"documents": "weekly"}}).status_code == 422
    body = s["c"]["user"].put(N, json={"choices": {"documents": "daily"}}).json()["choices"]
    assert body[0] == {"category": "documents", "label": "Your documents", "description": body[0]["description"],
                       "choice": "daily", "even_if_seen": True}
    ev = s["db"].scalar(select(AuditEvent).where(AuditEvent.action == "notifications.changed"))
    assert ev.details == {"documents": ["each", "daily"]}


def test_daily_summary_holds_and_combines(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    s["c"]["user"].put(N, json={"choices": {"documents": "daily"}})
    _finish(s, "first.pdf")
    _finish(s, "second.pdf")
    assert _to("user@a.test") == []  # held, not sent
    held = db.scalars(select(MailOutbox).where(MailOutbox.status == "held")).all()
    assert sorted(m.subject for m in held) == ["Done: first.pdf", "Done: second.pdf"]
    # Not before the summary time…
    first = min(m.created_at for m in held)
    assert notifications.send_digests(db, first - timedelta(hours=1)) == 0
    # …then one email with both
    assert notifications.send_digests(db, datetime.now(timezone.utc) + timedelta(days=1, hours=1)) == 1
    outbox.send_queued(db)
    mail, = _to("user@a.test")
    assert mail.subject.endswith("2 notifications from the last day")
    assert "Done: first.pdf" in mail.text and "Done: second.pdf" in mail.text and "<h2" in mail.html
    assert {m.status for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "job"))} == {"combined"}
    assert notifications.send_digests(db, datetime.now(timezone.utc) + timedelta(days=2)) == 0  # nothing twice


def test_off_sends_nothing_and_the_default_applies_to_everyone_else(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    s["c"]["user"].put(N, json={"choices": {"documents": "off"}})
    _finish(s, "quiet.pdf")
    assert _to("user@a.test") == [] and not db.scalar(select(MailOutbox).where(MailOutbox.to_addr == "user@a.test"))
    # A super admin's default: once a day for people who haven't chosen
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"notify_default_documents": "daily"}).status_code == 200
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"notify_default_help": "sometimes"}).status_code == 422
    _finish(s, "later.pdf", who="user2")
    assert db.scalar(select(MailOutbox).where(MailOutbox.to_addr == "user2@a.test")).status == "held"
    assert s["c"]["user2"].get(N).json()["choices"][0]["choice"] == "daily"


def test_sign_in_codes_and_other_emails_are_never_held(setup, make_client):
    s, db = setup, setup["db"]
    s["c"]["user"].put(N, json={"choices": {"documents": "off", "help": "off"}})
    assert notifications.status_for(db, "user@a.test", "account") == "queued"
    assert notifications.status_for(db, "user@a.test", "report") == "queued"
    get_mailer().outbox.clear()
    assert make_client().post(f"{API}/auth/code", json={"email": "user@a.test"}).status_code == 202
    assert _to("user@a.test")[0].subject.startswith("Your sign-in code")


# --- not emailing what they already saw in the portal (#36) ----------------------------------------------

def _job_mail(db):
    return db.scalar(select(MailOutbox).where(MailOutbox.kind == "job").order_by(MailOutbox.created_at.desc()))


def test_a_document_watched_in_the_portal_isnt_emailed(setup, monkeypatch):
    s, db = setup, setup["db"]
    monkeypatch.setattr(notifications, "SEEN_DELAY", timedelta(minutes=2))
    topup(s, s["a"].id, 100)
    body = s["c"]["user"].put(N, json={"even_if_seen": {"documents": False}}).json()["choices"]
    assert [(c["category"], c["even_if_seen"]) for c in body] == [("documents", False), ("help", False)]
    job_id = upload(s["c"]["user"], ("watched.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(db)
    assert s["c"]["user"].post(f"{API}/me/seen", json={"items": [f"job:{job_id}", "nonsense", "help:not-an-id"]}).status_code == 204
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    row = _job_mail(db)
    assert row.about == f"job:{job_id}" and row.next_try_at > datetime.now(timezone.utc)  # waits a moment
    db.execute(MailOutbox.__table__.update().values(next_try_at=MailOutbox.created_at))
    db.commit()
    outbox.send_queued(db)
    db.refresh(row)
    assert row.status == "seen" and _to("user@a.test") == []


def test_not_looking_or_asking_for_every_email_sends_it(setup, monkeypatch):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    # Document emails still come by default even when seen (people keep them as a record)
    assert [c["even_if_seen"] for c in s["c"]["user"].get(N).json()["choices"]] == [True, False]
    s["c"]["user"].post(f"{API}/me/seen", json={"items": ["jobs"]})
    _finish(s, "anyway.pdf")
    assert [m.subject for m in _to("user@a.test")] == ["Done: anyway.pdf"]
    # Someone who skips seen ones but saw the page long before the news still gets it
    s["c"]["user2"].put(N, json={"even_if_seen": {"documents": False}})
    s["c"]["user2"].post(f"{API}/me/seen", json={"items": ["jobs"]})
    from portal.models import UserPresence
    db.execute(UserPresence.__table__.update().values(seen_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    db.commit()
    _finish(s, "away.pdf", who="user2")
    assert [m.subject for m in _to("user2@a.test")] == ["Done: away.pdf"]


def test_seen_items_are_left_out_of_the_daily_summary(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    s["c"]["user"].put(N, json={"choices": {"documents": "daily"}, "even_if_seen": {"documents": False}})
    _finish(s, "unseen.pdf")
    _finish(s, "seen.pdf")
    # Opening a document after it finished counts too: they've seen it, so the summary needn't say it again
    seen_id = db.scalar(select(Job.id).where(Job.filename == "seen.pdf"))
    s["c"]["user"].post(f"{API}/me/seen", json={"items": [f"job:{seen_id}"]})
    notifications.send_digests(db, datetime.now(timezone.utc) + timedelta(days=1, hours=1))
    outbox.send_queued(db)
    mail, = _to("user@a.test")
    assert "unseen.pdf" in mail.text and "Done: seen.pdf" not in mail.text


def test_help_chat_seen_in_the_portal_is_skipped_by_default(setup):
    s, db = setup, setup["db"]
    help_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    user = db.scalar(select(User).where(User.email == "user@a.test"))
    notifications.mark_seen(db, user, [f"help:{help_id}"])
    db.commit()
    assert notifications.seen(db, "user@a.test", f"help:{help_id}", now) is True
    assert notifications.seen(db, "user@a.test", f"help:{uuid.uuid4()}", now) is False  # another request
    s["c"]["user"].put(N, json={"even_if_seen": {"help": True}})
    db.expire_all()
    assert notifications.seen(db, "user@a.test", f"help:{help_id}", now) is False
    assert s["c"]["user"].put(N, json={"even_if_seen": {"admin": True}}).status_code == 422
