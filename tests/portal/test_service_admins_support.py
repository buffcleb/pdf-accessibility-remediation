"""#564 part 2: service admins as support. Help requests, the queues, Needs attention, System, costs, every report and
the audit log, and the emails super admins get, which a service admin may turn down in Settings."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from portal import alerts, approval, email_reports, notifications
from portal.mail import get_mailer
from portal.models import AuditEvent, Group, HelpRequest, Job, User
from tests.portal.conftest import login, make_group, make_user, sent_mail
from tests.portal.test_help import _ask, _completed
from tests.portal.test_jobs import API, fake_engine, run_worker, setup, topup, upload  # noqa: F401

SVC = "svc@admin.test"


@pytest.fixture
def s(setup, make_client):
    """test_jobs' setup (groups A and B, a super admin, A's and B's admins, two of A's people) and a service admin."""
    make_user(setup["db"], SVC, None, role="service_admin")
    setup["c"]["service"] = make_client()
    login(setup["c"]["service"], SVC)
    return setup


def _mails(to):
    return [m for m in sent_mail() if m.to == to]


# --- help requests -------------------------------------------------------------------------------------

def test_service_admins_handle_help_requests_as_support(s):
    req = _ask(s, _completed(s)).json()
    assert [m.subject for m in _mails(SVC) if m.subject.startswith("Help")] == ["Help request: doc.pdf"]  # as supers
    base = f"{API}/help/{req['id']}"
    svc = s["c"]["service"]
    assert [q["id"] for q in svc.get(f"{API}/admin/help").json()] == [req["id"]]
    assert svc.get(f"{API}/admin/help/list").json()["needs_us"] == 1
    assert svc.get(f"{API}/me").json()["help_needs_us"] == 1

    r = svc.post(f"{base}/assign", json={"take": True}).json()
    assert r["assignee_email"] == SVC and r["status"] == "in_progress" and r["role"] == "service_admin"
    assert r["pages_charged"] == 2
    get_mailer().outbox.clear()
    r = svc.post(f"{base}/messages", data={"body": "Which chart do you mean?"})
    assert r.status_code == 201 and r.json()["needs_staff"] is False
    assert "Which chart do you mean?" in _mails("user@a.test")[-1].text  # the person hears from support
    assert svc.post(f"{base}/messages", data={"body": "Probably the first.", "internal": "true"}).status_code == 201
    for who in ("super", "service"):
        assert "note" in [m["kind"] for m in s["c"][who].get(base).json()["messages"]], who
    assert "note" not in [m["kind"] for m in s["c"]["user"].get(base).json()["messages"]]

    assert svc.post(f"{base}/status", json={"status": "waiting"}).json()["status"] == "waiting"
    get_mailer().outbox.clear()
    r = s["c"]["user"].post(f"{base}/messages", data={"body": "The first one."}).json()
    assert r["status"] == "in_progress" and r["needs_staff"] is True
    s["db"].get(HelpRequest, uuid.UUID(req["id"])).staff_notified_at = None
    s["db"].commit()
    r = svc.post(f"{base}/resolve", json={"note": "Described the chart.", "refund_pages": 1})
    assert r.status_code == 200 and r.json()["pages_refunded"] == 1
    assert svc.post(f"{base}/save", json={"saved": True}).status_code == 200
    assert svc.post(f"{base}/reopen").status_code == 200  # support may reopen any time


def test_a_reply_goes_to_the_service_admin_who_took_it(s):
    req = _ask(s, _completed(s)).json()
    s["c"]["service"].post(f"{API}/help/{req['id']}/assign", json={"take": True})
    hr = s["db"].get(HelpRequest, uuid.UUID(req["id"]))
    from portal import helpdesk
    assert helpdesk._staff_recipients(s["db"], hr) == [SVC]
    hr.assignee_id = hr.assignee_email = None
    assert helpdesk._staff_recipients(s["db"], hr) == ["root@admin.test", SVC]


# --- the queues, reruns, removing at once ------------------------------------------------------------

def test_service_admins_see_the_queues_and_act_on_items_but_dont_pause_them(s):
    svc = s["c"]["service"]
    keys = [q["key"] for q in svc.get(f"{API}/admin/queues").json()]
    assert keys and svc.get(f"{API}/admin/queues/live").status_code == 200
    assert svc.get(f"{API}/admin/queues/{keys[0]}/items").status_code == 200
    assert svc.post(f"{API}/admin/queues/{keys[0]}/pause", json={}).status_code == 403
    assert svc.post(f"{API}/admin/queues/{keys[0]}/resume").status_code == 403
    assert s["c"]["admin_a"].get(f"{API}/admin/queues").status_code == 403


def test_service_admins_rerun_and_remove_documents(s):
    job_id = _completed(s)
    svc = s["c"]["service"]
    r = svc.post(f"{API}/jobs/{job_id}/rerun", json={"reason": "Asked for in a help request"})
    assert r.status_code == 201, r.text
    r = svc.post(f"{API}/admin/jobs/remove", json={"ids": [job_id], "reason": "Asked for"})
    assert r.status_code == 200, r.text
    assert s["c"]["admin_a"].post(f"{API}/jobs/{job_id}/rerun", json={}).status_code == 403


# --- costs, reports, the audit log -------------------------------------------------------------------

def test_service_admins_see_dollar_costs(s):
    job_id = _completed(s)
    job = s["db"].get(Job, uuid.UUID(job_id))
    job.cost = {**(job.cost or {}), "ai_usd": 0.2767}
    s["db"].commit()
    assert s["c"]["service"].get(f"{API}/jobs/{job_id}").json()["cost"]["ai_usd"] == 0.2767
    docs = s["c"]["service"].get(f"{API}/groups/{s['a'].id}/documents").json()["items"]
    assert docs[0]["cost"]["ai_usd"] == 0.2767
    assert s["c"]["admin_a"].get(f"{API}/jobs/{job_id}").json()["cost"] is None


def test_service_admins_see_every_report_for_every_group_and_the_audit_log(s):
    svc, admin = s["c"]["service"], s["c"]["admin_a"]
    mine = {r["name"] for r in svc.get(f"{API}/reports").json()}
    supers = {r["name"] for r in s["c"]["super"].get(f"{API}/reports").json()}
    assert mine == supers and mine > {r["name"] for r in admin.get(f"{API}/reports").json()}
    assert svc.get(f"{API}/reports/audit").status_code == 200
    assert svc.get(f"{API}/admin/reports/groups.csv").status_code == 200
    assert svc.get(f"{API}/admin/audit").status_code == 200
    exported = s["db"].scalar(select(AuditEvent).where(AuditEvent.action == "report.exported",
                                                       AuditEvent.actor_email == SVC))
    assert exported.details["scope"] == "all groups"
    assert admin.get(f"{API}/admin/audit").status_code == 403


# --- Needs attention, System -------------------------------------------------------------------------

def test_service_admins_see_needs_attention_and_the_system_pages(s):
    svc = s["c"]["service"]
    assert svc.get(f"{API}/admin/attention").status_code == 200
    assert svc.get(f"{API}/admin/attention/acknowledged").status_code == 200
    r = svc.get(f"{API}/admin/access-check", params={"email": "user@a.test", "service": "standard"})
    assert r.status_code == 200, r.text
    assert [x["key"] for x in svc.get(f"{API}/admin/services").json()] == ["standard"]  # read only
    assert svc.patch(f"{API}/admin/services/standard", json={"enabled": False}).status_code == 403
    for path in ("/admin/monitoring/system", "/admin/monitoring/workloads", "/admin/audit/siem"):
        assert svc.get(API + path).status_code == 200, path
    assert svc.post(f"{API}/admin/monitoring/grafana").status_code == 403


# --- emails ------------------------------------------------------------------------------------------

def test_service_admins_get_alerts_unless_they_turn_them_down(s):
    db = s["db"]
    svc = db.scalar(select(User).where(User.email == SVC))
    assert set(notifications.choices(db, svc)) == {"documents", "help", "admin", "alerts"}
    root = db.scalar(select(User).where(User.email == "root@admin.test"))
    assert "alerts" not in notifications.choices(db, root)  # super admins always get them at once
    get_mailer().outbox.clear()
    alerts.mail_super_admins(db, "queue_stuck", "Alert: stuck", "Work is stuck.")
    db.commit()
    assert {m.to for m in sent_mail()} == {"root@admin.test", SVC}
    r = s["c"]["service"].put(f"{API}/me/notifications", json={"choices": {"alerts": "off"}})
    assert r.status_code == 200, r.text
    db.expire_all()  # this session saw the old choice
    get_mailer().outbox.clear()
    alerts.mail_super_admins(db, "queue_stuck", "Still: stuck", "Work is stuck.")
    alerts.mail_super_admins(db, "security", "Security alert", "Look.", service_admins=False)
    db.commit()
    assert sorted(m.subject for m in sent_mail()) == ["Security alert", "Still: stuck"]
    assert {m.to for m in sent_mail()} == {"root@admin.test"}


def test_a_group_with_no_admin_asks_support_to_approve(s):
    g = make_group(s["db"], "Campus C", "c.test")
    assert approval._admins_of(s["db"], g) == ["root@admin.test", SVC]


def test_service_admins_may_have_the_service_summary(s):
    svc = s["db"].scalar(select(User).where(User.email == SVC))
    assert email_reports.eligible(svc) == ["service_summary"]
