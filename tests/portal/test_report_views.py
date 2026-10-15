"""Scheduled report views: saved views of live reports, emailed each period to people with the same access."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from portal import email_reports, outbox, report_views
from portal.email_reports import _first_working_day
from portal.mail import get_mailer
from portal.models import AuditEvent, Job, ReportSend, RoleAssignment, SavedReport, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

V = f"{API}/me/report-views"


def _done(s, client, name, pages):
    job_id = upload(s["c"][client], (name, pdf_bytes(pages))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])


@pytest.fixture
def data(setup):
    s = setup
    topup(s, s["a"].id, 100)
    _done(s, "user", "annual-report.pdf", 3)
    _done(s, "user2", "minutes.pdf", 1)
    get_mailer().outbox.clear()
    today = datetime.now(timezone.utc).date()
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    s["due"] = datetime.combine(_first_working_day(nxt), datetime.min.time(), timezone.utc) + timedelta(hours=8)
    s["ids"] = {u.email: str(u.id) for u in s["db"].scalars(select(User))}
    # Views made "earlier", so this month's period counts
    return s


def _backdate(db):
    db.execute(SavedReport.__table__.update().values(created_at=datetime.now(timezone.utc) - timedelta(days=40)))
    db.commit()


def _sent(db):
    outbox.send_queued(db)
    return [m for m in get_mailer().outbox]


def test_recipients_need_the_same_access(data):
    s = data
    sup, ids = s["c"]["super"], s["ids"]
    every = [p["email"] for p in sup.get(f"{V}/recipients", params={"report": "documents"}).json()]
    assert every == ["root@admin.test"]  # all groups: super admins only
    one = [p["email"] for p in sup.get(f"{V}/recipients", params={"report": "documents", "group_id": str(s["a"].id)}).json()]
    assert one == ["admin@a.test", "root@admin.test"]
    r = sup.post(V, json={"name": "All documents", "report": "documents", "recipients": [ids["admin@a.test"]]})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bad_recipients"
    # Group admins can't schedule super-admin reports, or another group's
    a = s["c"]["admin_a"]
    assert a.post(V, json={"name": "Cost", "report": "cost"}).status_code == 404
    assert a.post(V, json={"name": "B", "report": "documents", "group_id": str(s["b"].id)}).status_code == 404


def test_a_view_is_emailed_each_period_with_its_filters_and_a_csv(data):
    s, db, ids = data, data["db"], data["ids"]
    r = s["c"]["super"].post(V, json={
        "name": "Campus A documents", "report": "documents", "group_id": str(s["a"].id),
        "params": {"q": "annual", "sort": "-created_at", "range": "7d", "page": "3"},
        "recipients": [ids["root@admin.test"], ids["admin@a.test"]], "attach_csv": True})
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["params"] == {"q": "annual", "sort": "-created_at"}  # the period replaces the saved range
    assert [p["email"] for p in view["recipients"]] == ["admin@a.test", "root@admin.test"]
    _backdate(db)
    assert report_views.send_due(db, s["due"]) == 2
    mails = {m.to: m for m in _sent(db)}
    m = mails["admin@a.test"]
    assert m.subject.startswith("Campus A documents: ") and "annual-report.pdf" in m.text and "minutes.pdf" not in m.text
    assert "<table" in m.html and 'scope="col"' in m.html and "/unsubscribe?t=" in m.html
    assert "range=custom" in m.text and f"/admin/groups/{s['a'].id}/reports" in m.text
    (name, ctype, csv_bytes), = m.attachments
    assert name.endswith(".csv") and ctype.startswith("text/csv") and b"annual-report.pdf" in csv_bytes
    # Once per period, and recorded
    assert report_views.send_due(db, s["due"] + timedelta(hours=2)) == 0
    assert db.scalar(select(ReportSend).where(ReportSend.saved_report_id == uuid.UUID(view["id"]),
                                              ReportSend.email == "admin@a.test")).status == "queued"
    # The built-in reports for the same period still go
    assert email_reports.send_due(db, s["due"]) >= 1


def test_the_csv_follows_what_the_mail_service_can_carry(data, monkeypatch):
    """Cloudflare's API takes attachments up to its message size; a service without attachments gets a note."""
    from portal import mail as mailmod
    from portal.config import get_portal_settings

    s, db, ids = data, data["db"], data["ids"]
    ps = get_portal_settings()

    def send(backend, name):
        r = s["c"]["super"].post(V, json={"name": name, "report": "documents", "group_id": str(s["a"].id),
                                          "recipients": [ids["root@admin.test"]], "attach_csv": True})
        assert r.status_code == 201, r.text
        _backdate(db)
        monkeypatch.setattr(ps, "mail_backend", backend)  # decides what the email is built with
        report_views.send_due(db, s["due"])
        monkeypatch.setattr(ps, "mail_backend", "memory")  # ...and nothing is really sent
        return next(m for m in _sent(db) if m.subject.startswith(name))

    m = send("cloudflare", "Fits")
    assert len(m.attachments) == 1 and "too big" not in m.text and "can't send" not in m.text
    monkeypatch.setattr(mailmod, "CLOUDFLARE_ATTACHMENT_BYTES", 10)
    m = send("cloudflare", "Too big")
    assert m.attachments == [] and "too big to attach" in m.text
    m = send("plunk", "No attachments")
    assert m.attachments == [] and "can't send attachments" in m.text


def test_access_is_checked_when_it_is_sent(data):
    s, db, ids = data, data["db"], data["ids"]
    s["c"]["super"].post(V, json={"name": "A docs", "report": "documents", "group_id": str(s["a"].id),
                                  "recipients": [ids["root@admin.test"], ids["admin@a.test"]]})
    _backdate(db)
    db.execute(RoleAssignment.__table__.delete().where(RoleAssignment.user_id == uuid.UUID(ids["admin@a.test"])))
    db.commit()
    db.expire_all()
    report_views.send_due(db, s["due"])
    assert [m.to for m in _sent(db)] == ["root@admin.test"]


def test_owner_losing_access_stops_the_view(data):
    s, db, ids = data, data["db"], data["ids"]
    r = s["c"]["admin_a"].post(V, json={"name": "Mine", "report": "documents", "group_id": str(s["a"].id)})
    assert r.status_code == 201 and [p["email"] for p in r.json()["recipients"]] == ["admin@a.test"]
    _backdate(db)
    db.execute(RoleAssignment.__table__.delete().where(RoleAssignment.user_id == uuid.UUID(ids["admin@a.test"])))
    db.commit()
    db.expire_all()
    assert report_views.send_due(db, s["due"]) == 0


def test_managing_leaving_and_the_unsubscribe_link(data):
    s, db, ids = data, data["db"], data["ids"]
    sup, a = s["c"]["super"], s["c"]["admin_a"]
    vid = sup.post(V, json={"name": "A docs", "report": "documents", "group_id": str(s["a"].id),
                            "recipients": [ids["root@admin.test"], ids["admin@a.test"]]}).json()["id"]
    # The recipient sees it, can't change it, can leave it
    listed = a.get(V).json()
    assert listed[0]["id"] == vid and listed[0]["mine"] is False and listed[0]["owner"] == "root@admin.test"
    assert a.patch(f"{V}/{vid}", json={"frequency": "daily"}).status_code == 404
    assert sup.patch(f"{V}/{vid}", json={"frequency": "weekly", "attach_csv": True}).json()["frequency"] == "weekly"
    admin = db.scalar(select(User).where(User.email == "admin@a.test"))
    token = email_reports.unsubscribe_token(admin, f"view:{vid}")
    info = s["c"]["user"].get(f"{API}/email-reports/unsubscribe", params={"t": token}).json()
    assert info == {"report": "A docs", "email": "admin@a.test", "frequency": "weekly"}
    assert s["c"]["user"].post(f"{API}/email-reports/unsubscribe", json={"token": token}).json()["frequency"] == "off"
    assert a.get(V).json() == []
    assert sup.post(f"{V}/{vid}/sample").status_code == 202
    assert _sent(db)[-1].subject.startswith("Sample: A docs")
    assert sup.delete(f"{V}/{vid}").status_code == 204
    actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_type == "report_view")
                                            .order_by(AuditEvent.created_at))]
    assert actions == ["report_view.created", "report_view.changed", "report_view.left", "report_view.deleted"]
