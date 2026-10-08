"""#7: new members wait for a group admin's approval before they can submit anything."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal.mail import get_mailer
from portal.models import AuditEvent
from tests.portal.conftest import login, sent_mail
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, setup, topup, upload  # noqa: F401


def _require(s, on=True):
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"require_approval": on})
    assert r.status_code == 200 and r.json()["require_approval"] is on


def _newcomer(s, make_client, email="new@a.test"):
    c = make_client()
    login(c, email)
    return c, c.get(f"{API}/me").json()


def _mails_to(email):
    return [m for m in sent_mail() if m.to == email]


def test_new_member_waits_and_the_group_admins_are_told(setup, make_client):
    s = setup
    _require(s)
    topup(s, s["a"].id, 100)
    c, me = _newcomer(s, make_client)
    assert me["approval"] == "pending" and me["can_submit"] is False

    r = upload(c, ("x.pdf", pdf_bytes(1)))
    assert r.status_code == 403 and r.json()["detail"]["error"] == "pending_approval"

    mail = _mails_to("admin@a.test")[-1]
    assert "new@a.test" in mail.subject and f"/admin/groups/{s['a'].id}/people" in mail.text
    group = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()
    assert group["pending_members"] == 1
    assert s["c"]["admin_a"].get(f"{API}/me").json()["pending_approvals"] == 1
    kinds = [i["kind"] for i in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/attention").json()]
    assert "members_waiting" in kinds
    waiting = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/users", params={"approval": "pending"}).json()
    assert [u["email"] for u in waiting] == ["new@a.test"]
    actions = {e.action for e in s["db"].scalars(select(AuditEvent).where(AuditEvent.group_id == s["a"].id))}
    assert "user.approval_requested" in actions


def test_approval_lets_them_submit_and_tells_them(setup, make_client):
    s = setup
    _require(s)
    topup(s, s["a"].id, 100)
    c, me = _newcomer(s, make_client)
    r = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/users/{me['id']}/approve")
    assert r.status_code == 200 and r.json()["approval"] == "approved" and r.json()["approval_by"] == "admin@a.test"
    assert "You can now use" in _mails_to("new@a.test")[-1].subject
    assert c.get(f"{API}/me").json()["can_submit"] is True
    assert upload(c, ("x.pdf", pdf_bytes(1))).status_code == 200
    again = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/users/{me['id']}/approve")
    assert again.status_code == 409


def test_decline_with_a_reason_and_approve_later(setup, make_client):
    s = setup
    _require(s)
    c, me = _newcomer(s, make_client)
    base = f"{API}/groups/{s['a'].id}/users/{me['id']}"
    r = s["c"]["admin_a"].post(f"{base}/decline", json={"reason": "  Students use the library's service.  "})
    assert r.status_code == 200 and r.json()["approval"] == "declined"
    assert r.json()["decline_reason"] == "Students use the library's service."
    assert "library" in _mails_to("new@a.test")[-1].text

    r = upload(c, ("x.pdf", pdf_bytes(1)))
    assert r.status_code == 403 and r.json()["detail"]["error"] == "approval_declined"
    assert "library" in r.json()["detail"]["message"]
    assert s["c"]["admin_a"].post(f"{base}/decline").status_code == 409  # only people waiting can be declined

    assert s["c"]["admin_a"].post(f"{base}/approve").json()["approval"] == "approved"
    assert c.get(f"{API}/me").json()["decline_reason"] is None


def test_waiting_people_cant_edit_review_or_confirm(setup, make_client):
    s = setup
    _require(s)
    c, _ = _newcomer(s, make_client)
    job = uuid.uuid4()
    for path, body in [(f"/jobs/{job}/confirm", None), (f"/jobs/{job}/versions", {"based_on": 1, "edits": [{"op": "alt", "i": 0, "alt": "x"}]}),
                       (f"/jobs/{job}/versions/1/review", None)]:
        r = c.post(f"{API}{path}", json=body)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "pending_approval", path


def test_only_the_groups_admins_and_super_admins_decide(setup, make_client):
    s = setup
    _require(s)
    _, me = _newcomer(s, make_client)
    path = f"{API}/groups/{s['a'].id}/users/{me['id']}/approve"
    assert s["c"]["admin_b"].post(path).status_code == 404
    assert s["c"]["user"].post(path).status_code == 404
    assert s["c"]["super"].post(path).json()["approval"] == "approved"


def test_existing_members_and_groups_without_approval_are_unaffected(setup, make_client):
    s = setup
    topup(s, s["a"].id, 100)
    _, me = _newcomer(s, make_client, "fresh@a.test")  # approval is off for this group
    assert me["approval"] == "approved"
    _require(s)
    assert s["c"]["user"].get(f"{API}/me").json()["can_submit"] is True  # signed up before it was switched on
    assert upload(s["c"]["user"], ("x.pdf", pdf_bytes(1))).status_code == 200


def test_new_groups_follow_the_default_and_admins_are_approved(setup, make_client):
    s = setup
    sup = s["c"]["super"]
    g = sup.post(f"{API}/admin/groups", json={"name": "Campus C", "domains": ["c.test"]}).json()
    assert g["require_approval"] is True  # the default for new groups
    assert sup.patch(f"{API}/admin/settings", json={"new_groups_require_approval": False}).status_code == 200
    assert sup.post(f"{API}/admin/groups", json={"name": "Campus D"}).json()["require_approval"] is False

    _, me = _newcomer(s, make_client, "head@c.test")
    assert me["approval"] == "pending"
    r = sup.post(f"{API}/admin/groups/{g['id']}/admins", json={"email": "head@c.test"})
    assert r.status_code == 201 and r.json()["approval"] == "approved"


def test_access_check_and_usage_report_show_approval(setup, make_client):
    s = setup
    _require(s)
    _newcomer(s, make_client)
    check = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/access-check",
                                  params={"email": "new@a.test", "service": "standard"}).json()
    step = next(x for x in check["steps"] if x["key"] == "approved")
    assert check["can_use"] is False and step["ok"] is False and step["detail"] == "waiting for approval"

    csv = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/reports/usage.csv").text.splitlines()
    header = csv[0].split(",")
    assert header[4:7] == ["approval", "approval_date", "approved_by"]
    row = next(line for line in csv if "new@a.test" in line).split(",")
    assert row[4] == "pending"
