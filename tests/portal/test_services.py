"""Services: opt-in at global, group and user level, per-service quotas, data notices and cost guards."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal import worker
from portal.models import Group, Job, QuotaLedger, Service
from tests.portal.test_jobs import (  # noqa: F401  (fixtures)
    API, fake_engine, pdf_bytes, run_worker, setup, topup, upload,
)

EXTRA = "partner"


def _add_service(db, **kw):
    """A second processing service like Adobe: sends documents off campus, switched off until enabled."""
    fields = dict(key=EXTRA, kind="processing", label="Partner tagging", enabled=False, external=True,
                  notice_enabled=False, metered=True, used_pages=0, reserved_pages=0, confirm_before_send=False, sort=10)
    s = Service(**{**fields, **kw})
    db.add(s)
    db.commit()
    return s


def _upload_to(client, service, *docs, confirm=False):
    files = [("files", (name, data, "application/pdf")) for name, data in docs]
    return client.post(f"{API}/jobs", files=files, data={"processing": service, "confirm": str(confirm).lower()})


def _enable_everywhere(s, *, grant_all=True, pages=100):
    sup, a = s["c"]["super"], s["a"].id
    assert sup.patch(f"{API}/admin/services/{EXTRA}", json={"enabled": True}).status_code == 200
    r = s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{EXTRA}", json={"enabled": True, "all_users": grant_all})
    assert r.status_code == 200, r.text
    if pages:
        assert topup(s, a, pages, service=EXTRA).status_code == 201


def test_service_needs_global_group_and_user_opt_in(setup):
    s = setup
    _add_service(s["db"])
    user, a = s["c"]["user"], s["a"].id
    topup(s, a, 50)
    doc = ("doc.pdf", pdf_bytes(2))

    def refused():
        r = _upload_to(user, EXTRA, doc).json()
        if isinstance(r, list):
            return r[0]["error"]["error"]
        return r["detail"]["error"]

    assert refused() == "service_disabled"
    assert [x["key"] for x in user.get(f"{API}/services").json()] == ["standard"]
    # Group admins can't switch on a service that's off globally
    r = s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{EXTRA}", json={"enabled": True})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "service_disabled"
    s["c"]["super"].patch(f"{API}/admin/services/{EXTRA}", json={"enabled": True})
    assert refused() == "group_not_enabled"
    s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{EXTRA}", json={"enabled": True})
    assert refused() == "not_granted"
    uid = next(u["id"] for u in s["c"]["admin_a"].get(f"{API}/groups/{a}/users").json() if u["email"] == "user@a.test")
    r = s["c"]["admin_a"].put(f"{API}/groups/{a}/users/{uid}/services/{EXTRA}", json={"granted": True})
    assert r.status_code == 200 and next(x for x in r.json()["services"] if x["key"] == EXTRA)["granted"]
    # Granted but no pages in the group's pool for this service
    r = _upload_to(user, EXTRA, doc).json()
    assert isinstance(r, list), r
    r = r[0]
    assert r["error"]["error"] == "over_quota" and r["error"]["service"] == EXTRA
    topup(s, a, 10, service=EXTRA)
    r = _upload_to(user, EXTRA, doc).json()[0]
    assert r["accepted"] and r["job"]["processing"] == EXTRA and r["job"]["charged_services"] == [EXTRA]
    assert r["job"]["external_services"] == [EXTRA]
    # Another member of the group isn't granted it
    r = _upload_to(s["c"]["user2"], EXTRA, doc)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "not_granted"
    # The standard pool is untouched
    me = user.get(f"{API}/me").json()
    assert me["reserved_pages"] == 0
    extra = next(x for x in me["services"] if x["key"] == EXTRA)
    assert extra["reserved_pages"] == 2 and extra["remaining_pages"] == 8


def test_local_only_group_blocks_external_services(setup):
    s = setup
    _add_service(s["db"])
    _enable_everywhere(s)
    a = s["a"].id
    r = s["c"]["admin_a"].patch(f"{API}/groups/{a}", json={"local_only": True})
    assert r.status_code == 200 and r.json()["local_only"]
    r = _upload_to(s["c"]["user"], EXTRA, ("doc.pdf", pdf_bytes(1))).json()
    assert r["detail"]["error"] == "local_only"
    svc = next(x for x in r_group(s) if x["key"] == EXTRA)
    assert svc["enabled"] and not svc["available"]
    # And it can't be switched on again while local-only
    s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{EXTRA}", json={"enabled": False})
    r = s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{EXTRA}", json={"enabled": True})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "local_only"
    # The standard service isn't external and keeps working
    topup(s, a, 5)
    assert upload(s["c"]["user"], ("doc.pdf", pdf_bytes(1))).json()[0]["accepted"]


def r_group(s):
    return s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()["services"]


def test_notice_and_per_job_ceiling_are_shown_and_enforced(setup):
    s = setup
    _add_service(s["db"], max_pages_per_job=3, notice_text="Documents are sent to Partner Inc. for tagging.")
    _enable_everywhere(s)
    sup = s["c"]["super"]
    choice = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == EXTRA)
    assert choice["notice"] is None  # the notice is off until switched on
    sup.patch(f"{API}/admin/services/{EXTRA}", json={"notice_enabled": True})
    choice = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == EXTRA)
    assert choice["notice"].startswith("Documents are sent") and choice["max_pages_per_job"] == 3
    r = _upload_to(s["c"]["user"], EXTRA, ("big.pdf", pdf_bytes(4)), ("ok.pdf", pdf_bytes(3))).json()
    assert r[0]["error"]["error"] == "too_many_pages_for_service" and r[1]["accepted"]


def test_confirm_before_send_holds_until_confirmed(setup):
    s = setup
    _add_service(s["db"], confirm_before_send=True)
    _enable_everywhere(s, pages=10)
    user = s["c"]["user"]
    r = _upload_to(user, EXTRA, ("doc.pdf", pdf_bytes(4))).json()[0]
    job = r["job"]
    assert job["status"] == "held" and job["needs_confirmation"] and job["pages_charged"] == 0
    run_worker(s["db"])  # held jobs are never sent
    assert not s["engine"].submitted
    extra = next(x for x in user.get(f"{API}/me").json()["services"] if x["key"] == EXTRA)
    assert extra["reserved_pages"] == 0
    r = user.post(f"{API}/jobs/{job['id']}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["pages_charged"] == 4
    assert user.post(f"{API}/jobs/{job['id']}/confirm").status_code == 409
    # A second held document can be canceled instead, without having used any pages
    job2 = _upload_to(user, EXTRA, ("doc2.pdf", pdf_bytes(2))).json()[0]["job"]
    assert user.post(f"{API}/jobs/{job2['id']}/cancel").json()["status"] == "canceled"
    # Already confirmed at upload: queued straight away
    r = _upload_to(user, EXTRA, ("doc3.pdf", pdf_bytes(1)), confirm=True).json()[0]
    assert r["job"]["status"] == "queued"


def test_daily_cap_holds_jobs_back_without_failing_them(setup):
    s = setup
    _add_service(s["db"], daily_cap_pages=5)
    _enable_everywhere(s, pages=100)
    user = s["c"]["user"]
    ids = [x["job"]["id"] for x in _upload_to(user, EXTRA, ("a.pdf", pdf_bytes(3)), ("b.pdf", pdf_bytes(3))).json()]
    worker.submit_queued(s["db"])
    jobs = {j.id: j for j in s["db"].scalars(select(Job))}
    first, second = jobs[uuid.UUID(ids[0])], jobs[uuid.UUID(ids[1])]
    assert first.status == "submitted" and second.status == "queued"
    assert second.progress == {"stage": "waiting_daily_cap", "services": [EXTRA]}
    svc = next(x for x in s["c"]["super"].get(f"{API}/admin/services").json() if x["key"] == EXTRA)
    assert svc["pages_sent_today"] == 3
    # Raising the cap lets it go
    s["c"]["super"].patch(f"{API}/admin/services/{EXTRA}", json={"daily_cap_pages": 10})
    worker.submit_queued(s["db"])
    s["db"].refresh(second)
    assert second.status == "submitted"


def test_global_pool_limits_every_group(setup):
    s = setup
    _add_service(s["db"])
    _enable_everywhere(s, pages=100)
    sup = s["c"]["super"]
    r = sup.patch(f"{API}/admin/services/{EXTRA}", json={"global_limited": True})
    assert r.json()["global_limited"] and r.json()["remaining_pages"] == 0
    err = _upload_to(s["c"]["user"], EXTRA, ("doc.pdf", pdf_bytes(2))).json()[0]["error"]
    assert err["error"] == "over_quota" and err["scope"] == "global"
    r = sup.post(f"{API}/admin/services/{EXTRA}/topups", json={"pages": 3, "ticket_ref": "RITM1"})
    assert r.status_code == 201 and r.json()["scope"] == "global" and r.json()["group_id"] is None
    assert _upload_to(s["c"]["user"], EXTRA, ("doc.pdf", pdf_bytes(2))).json()[0]["accepted"]
    err = _upload_to(s["c"]["user"], EXTRA, ("doc.pdf", pdf_bytes(2))).json()[0]["error"]
    assert err["scope"] == "global" and err["remaining_pages"] == 1
    # Global top-ups follow the same ticket rule as group ones
    r = sup.post(f"{API}/admin/services/{EXTRA}/topups", json={"pages": 3})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "ticket_required"


def test_completion_and_failure_settle_the_right_service(setup):
    s = setup
    _add_service(s["db"])
    _enable_everywhere(s, pages=20)
    user, db = s["c"]["user"], s["db"]
    ids = [x["job"]["id"] for x in _upload_to(user, EXTRA, ("a.pdf", pdf_bytes(3)), ("b.pdf", pdf_bytes(2))).json()]
    run_worker(db)
    jobs = {str(j.id): j for j in db.scalars(select(Job))}
    s["engine"].complete(jobs[ids[0]].engine_job_id)
    s["engine"].fail(jobs[ids[1]].engine_job_id, "pdf_unreadable")
    run_worker(db)
    extra = next(x for x in user.get(f"{API}/me").json()["services"] if x["key"] == EXTRA)
    assert extra["used_pages"] == 3 and extra["reserved_pages"] == 0
    g = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()
    gs = next(x for x in g["services"] if x["key"] == EXTRA)["balance"]
    assert gs == {"pool_pages": 20, "used_pages": 3, "reserved_pages": 0, "remaining_pages": 17}
    assert g["balance"]["used_pages"] == 0  # standard untouched
    kinds = [(e.service_key, e.kind) for e in db.scalars(select(QuotaLedger).order_by(QuotaLedger.id))]
    assert (EXTRA, "commit") in kinds and (EXTRA, "release") in kinds and ("standard", "commit") not in kinds


def test_standard_service_cannot_be_switched_off(setup):
    s = setup
    r = s["c"]["super"].patch(f"{API}/admin/services/standard", json={"enabled": False})
    assert r.status_code == 422
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/services/standard", json={"enabled": False})
    assert r.status_code == 422


def test_new_groups_get_the_standard_service(setup):
    s = setup
    r = s["c"]["super"].post(f"{API}/admin/groups", json={"name": "Campus C", "domains": ["c.test"],
                                                          "default_user_cap": 7})
    assert r.status_code == 201
    std = next(x for x in r.json()["services"] if x["key"] == "standard")
    assert std["enabled"] and std["all_users"] and std["default_user_cap"] == 7 and r.json()["default_user_cap"] == 7
    g = s["db"].scalar(select(Group).where(Group.name == "Campus C"))
    assert g.service("standard").is_default


def _uid(s, email):
    return next(u["id"] for u in s["c"]["super"].get(f"{API}/admin/users").json() if u["email"] == email)


def test_super_admin_corrects_usage_and_it_is_audited_for_both_logs(setup):
    s = setup
    a = s["a"].id
    topup(s, a, 50)
    user, sup, db = s["c"]["user"], s["c"]["super"], s["db"]
    jid = upload(user, ("doc.pdf", pdf_bytes(6))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(jid)).engine_job_id)
    run_worker(db)
    uid = _uid(s, "user@a.test")
    url = f"{API}/admin/users/{uid}/usage-corrections"
    # Give 4 pages back after a bad run
    r = sup.post(url, json={"pages": -4, "comment": "Run failed on our side", "ticket_ref": "RITM42"})
    assert r.status_code == 201, r.text
    assert r.json()["used_pages"] == 2
    g = s["c"]["admin_a"].get(f"{API}/groups/{a}").json()["balance"]
    assert g["used_pages"] == 2 and g["remaining_pages"] == 48
    # Can't give back more than was used, can't charge past the pool, and a reason is required
    assert sup.post(url, json={"pages": -3, "comment": "x"}).json()["detail"]["error"] == "below_zero"
    assert sup.post(url, json={"pages": 49, "comment": "x"}).json()["detail"]["error"] == "over_pool"
    assert sup.post(url, json={"pages": 1, "comment": ""}).status_code == 422
    # Charging more works too
    assert sup.post(url, json={"pages": 3, "comment": "Missed charge"}).json()["used_pages"] == 5
    entries = db.scalars(select(QuotaLedger).where(QuotaLedger.kind == "correct").order_by(QuotaLedger.id)).all()
    assert [(e.pages, e.ticket_ref) for e in entries] == [(-4, "RITM42"), (3, None)]
    # In the super admin's audit log and in the group's activity
    for client, path in ((sup, f"{API}/admin/audit"), (s["c"]["admin_a"], f"{API}/groups/{a}/audit")):
        ev = client.get(path, params={"action": "quota.usage_corrected"}).json()
        assert len(ev) == 2 and ev[-1]["details"]["used_pages"] == [6, 2] and ev[-1]["details"]["by_super_admin"]
    # Group admins can't correct usage
    assert s["c"]["admin_a"].post(url, json={"pages": -1, "comment": "x"}).status_code == 403


def test_super_admin_sets_limits_for_group_members_and_it_is_audited(setup):
    s = setup
    a = s["a"].id
    sup = s["c"]["super"]
    uid = _uid(s, "user@a.test")
    r = sup.put(f"{API}/groups/{a}/users/{uid}/services/standard", json={"cap_pages": 12})
    assert r.status_code == 200 and r.json()["cap_pages"] == 12 and r.json()["effective_cap"] == 12
    ev = s["c"]["admin_a"].get(f"{API}/groups/{a}/audit", params={"action": "quota.cap_set"}).json()
    assert ev[0]["details"]["cap_pages"] == [None, 12] and ev[0]["details"]["by_super_admin"]
    assert ev[0]["actor_email"] == "root@admin.test"
    # The group's own admin setting a limit isn't marked as a super admin's change
    s["c"]["admin_a"].put(f"{API}/groups/{a}/users/{uid}/services/standard", json={"cap_pages": 15})
    ev = sup.get(f"{API}/admin/audit", params={"action": "quota.cap_set"}).json()
    assert ev[0]["details"]["cap_pages"] == [12, 15] and not ev[0]["details"]["by_super_admin"]
