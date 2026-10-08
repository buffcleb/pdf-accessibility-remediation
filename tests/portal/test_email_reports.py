"""Scheduled email reports: periods, who gets what, once per period, opting out and unsubscribing."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from portal import email_reports, outbox
from portal.email_reports import _first_working_day, latest_period
from portal.mail import get_mailer
from portal.models import AuditEvent, Job, ReportSend, RoleAssignment, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

DENVER = ZoneInfo("America/Denver")


def test_periods_follow_the_service_time_zone():
    # November 2026 starts on a Sunday: the monthly report goes on Monday the 2nd at 07:00 Denver time
    assert _first_working_day(date(2026, 11, 15)) == date(2026, 11, 2)
    before = datetime(2026, 11, 2, 6, 59, tzinfo=DENVER)
    after = datetime(2026, 11, 2, 7, 0, tzinfo=DENVER)
    start, end, due = latest_period("monthly", after, DENVER, 7)
    assert (start.astimezone(DENVER).date(), end.astimezone(DENVER).date()) == (date(2026, 10, 1), date(2026, 11, 1))
    assert due == after.astimezone(timezone.utc)
    start, _, _ = latest_period("monthly", before, DENVER, 7)
    assert start.astimezone(DENVER).date() == date(2026, 9, 1)  # October's isn't due yet
    # Weekly: Monday to Sunday, sent on Monday; daily: the day before
    start, end, _ = latest_period("weekly", datetime(2026, 10, 14, 12, tzinfo=DENVER), DENVER, 7)
    assert (start.astimezone(DENVER).date(), end.astimezone(DENVER).date()) == (date(2026, 10, 5), date(2026, 10, 12))
    start, end, _ = latest_period("daily", datetime(2026, 10, 14, 12, tzinfo=DENVER), DENVER, 7)
    assert (start.astimezone(DENVER).date(), end.astimezone(DENVER).date()) == (date(2026, 10, 13), date(2026, 10, 14))


@pytest.fixture
def month(setup):
    """A finished document this month, and the moment this month's report is due (next month's first working day)."""
    s = setup
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("report.pdf", pdf_bytes(3))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    get_mailer().outbox.clear()
    today = datetime.now(timezone.utc).date()
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    s["due"] = datetime.combine(_first_working_day(nxt), datetime.min.time(), timezone.utc) + timedelta(hours=8)
    return s


def _sent(db):
    outbox.send_queued(db)
    return {m.to: m for m in get_mailer().outbox}


def test_each_person_gets_one_email_with_their_reports_once(month):
    s, db = month, month["db"]
    assert email_reports.send_due(db, s["due"]) >= 3
    mails = _sent(db)
    user, admin, root = mails["user@a.test"], mails["admin@a.test"], mails["root@admin.test"]
    assert "Your documents" in user.text and "Documents processed: 1" in user.text and "Group summary" not in user.text
    assert "<table" in user.html and '<th scope="col"' in user.html and "/unsubscribe?t=" in user.html
    assert "Group summary" in admin.text and "Campus A" in admin.text and "AI cost" not in admin.text
    assert "Service summary" in root.text and "AI cost" in root.text and "Pages by group" in root.text
    # Nothing happened for user2: no email, but the period is recorded as done
    assert "user2@a.test" not in mails
    assert db.scalar(select(ReportSend).where(ReportSend.email == "user2@a.test")).status == "skipped"
    # Never twice for the same period, even if the worker restarts
    assert email_reports.send_due(db, s["due"] + timedelta(hours=1)) == 0
    # Long after the due date, a missed send doesn't catch up
    db.execute(ReportSend.__table__.delete())
    db.commit()
    assert email_reports.send_due(db, s["due"] + timedelta(days=10)) == 0


def test_choices_and_the_unsubscribe_link(month):
    s, db = month, month["db"]
    c = s["c"]["user"]
    body = c.get(f"{API}/me/email-reports").json()
    assert [(r["kind"], r["frequency"]) for r in body["reports"]] == [("my_month", "monthly")]
    assert c.put(f"{API}/me/email-reports", json={"reports": {"my_month": "weekly", "service_summary": "daily"}}).status_code == 200
    assert [(r["kind"], r["frequency"]) for r in c.get(f"{API}/me/email-reports").json()["reports"]] == [("my_month", "weekly")]

    user = db.scalar(select(User).where(User.email == "user@a.test"))
    token = email_reports.unsubscribe_token(user, "my_month")
    anon = s["c"]["user2"]  # any client: the link needs no sign-in
    info = anon.get(f"{API}/email-reports/unsubscribe", params={"t": token}).json()
    assert info == {"report": "Your documents", "email": "user@a.test", "frequency": "weekly"}  # looking changes nothing
    assert anon.post(f"{API}/email-reports/unsubscribe", json={"token": token}).json()["frequency"] == "off"
    assert anon.post(f"{API}/email-reports/unsubscribe", json={"token": token[:-2] + "xx"}).status_code == 404
    actions = [e.details for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "email_report.changed"))]
    assert [a["how"] for a in actions] == ["settings", "unsubscribe link"]
    email_reports.send_due(db, s["due"])
    assert "user@a.test" not in _sent(db)


def test_access_is_checked_when_it_is_sent(month):
    s, db = month, month["db"]
    admin = db.scalar(select(User).where(User.email == "admin@a.test"))
    db.execute(RoleAssignment.__table__.delete().where(RoleAssignment.user_id == admin.id))
    db.scalar(select(User).where(User.email == "user@a.test")).status = "disabled"
    db.commit()
    db.expire_all()  # the worker starts each pass with a fresh session
    email_reports.send_due(db, s["due"])
    mails = _sent(db)
    assert "user@a.test" not in mails
    assert "Group summary" not in (mails.get("admin@a.test").text if "admin@a.test" in mails else "")


def test_a_sample_on_request_and_switching_it_all_off(month):
    s, db = month, month["db"]
    c = s["c"]["super"]
    r = c.post(f"{API}/me/email-reports/sample")
    assert r.status_code == 202 and r.json()["subject"].startswith("Sample: ")
    assert c.post(f"{API}/me/email-reports/sample").status_code == 429  # one a minute
    assert _sent(db)["root@admin.test"].subject.startswith("Sample: ")
    assert c.patch(f"{API}/admin/settings", json={"email_reports_enabled": False}).status_code == 200
    assert email_reports.send_due(db, s["due"]) == 0
    assert c.patch(f"{API}/admin/settings", json={"report_time_zone": "Nowhere/Land"}).status_code == 422


def test_super_admins_see_what_was_sent(month):
    s, db = month, month["db"]
    email_reports.send_due(db, s["due"])
    outbox.send_queued(db)
    body = s["c"]["super"].get(f"{API}/reports/report_emails").json()
    assert {r["delivery"] for r in body["rows"]} >= {"sent", "skipped"}
    assert s["c"]["admin_a"].get(f"{API}/reports/report_emails").status_code == 404
