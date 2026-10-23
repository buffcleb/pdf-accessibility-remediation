"""#14 part A: the Attention list and "why can't this person use this service?"."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal.models import Job, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from tests.portal.test_services import EXTRA, _add_service, _enable_everywhere


def _uid(s, email):
    return next(u["id"] for u in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/users").json() if u["email"] == email)


def test_personal_limit_with_an_empty_group_pool_is_explained(setup):
    """The faculty3 case: a 200-page personal limit, but no pages in the group's pool for the service."""
    s = setup
    _add_service(s["db"])
    _enable_everywhere(s, pages=0)
    a = s["c"]["admin_a"]
    upload(s["c"]["user"], ("x.pdf", pdf_bytes(1)))  # makes the user's account
    a.put(f"{API}/groups/{s['a'].id}/users/{_uid(s, 'user@a.test')}/services/{EXTRA}", json={"cap_pages": 200})

    check = s["c"]["super"].get(f"{API}/admin/access-check", params={"email": "user@a.test", "service": EXTRA}).json()
    assert check["can_use"] is False and check["remaining_pages"] == 0
    failing = [st for st in check["steps"] if not st["ok"] and st["blocking"]]
    assert [st["key"] for st in failing] == ["group_pages"]
    assert "personal limits can't give pages" in failing[0]["detail"] and "Services and pages" in failing[0]["fix"]
    person = next(st for st in check["steps"] if st["key"] == "person_pages")
    assert person["ok"] and "200" in person["detail"]  # their own limit is fine: the pool is the problem

    items = s["c"]["super"].get(f"{API}/admin/attention").json()
    empty = [i for i in items if i["kind"] == "group_pool_empty" and i["service_key"] == EXTRA]
    assert empty and empty[0]["severity"] == "bad" and "see 0 left" in empty[0]["detail"]
    assert items[0]["severity"] == "bad"  # most serious first
    # the group admin sees the same for their group, and can run the check for their members
    assert any(i["kind"] == "group_pool_empty" for i in a.get(f"{API}/groups/{s['a'].id}/attention").json())
    r = a.get(f"{API}/groups/{s['a'].id}/access-check", params={"email": "user@a.test", "service": EXTRA})
    assert r.status_code == 200 and r.json()["can_use"] is False

    topup(s, s["a"].id, 300, service=EXTRA)
    check = s["c"]["super"].get(f"{API}/admin/access-check", params={"email": "user@a.test", "service": EXTRA}).json()
    assert check["can_use"] and check["remaining_pages"] == 200


def test_each_kind_of_blocker_is_named(setup):
    s = setup
    _add_service(s["db"])
    upload(s["c"]["user"], ("x.pdf", pdf_bytes(1)))
    sup = s["c"]["super"]

    def first_failure():
        steps = sup.get(f"{API}/admin/access-check", params={"email": "user@a.test", "service": EXTRA}).json()["steps"]
        return next(st["key"] for st in steps if not st["ok"] and st["blocking"])

    assert first_failure() == "service_on"
    sup.patch(f"{API}/admin/services/{EXTRA}", json={"enabled": True})
    sup.patch(f"{API}/groups/{s['a'].id}", json={"local_only": True})
    assert first_failure() == "local_only"
    sup.patch(f"{API}/groups/{s['a'].id}", json={"local_only": False})
    assert first_failure() == "group_enabled"
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/services/{EXTRA}", json={"enabled": True, "all_users": False})
    assert first_failure() == "granted"


def test_access_checks_stay_within_the_group(setup):
    s = setup
    upload(s["c"]["user"], ("x.pdf", pdf_bytes(1)))
    r = s["c"]["admin_b"].get(f"{API}/groups/{s['b'].id}/access-check", params={"email": "user@a.test", "service": "standard"})
    assert r.status_code == 404  # not in group B
    assert s["c"]["admin_b"].get(f"{API}/groups/{s['a'].id}/attention").status_code == 404
    assert s["c"]["admin_a"].get(f"{API}/admin/attention").status_code == 403


def test_jobs_needing_attention(setup):
    s = setup
    topup(s, s["a"].id, 100)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        upload(s["c"]["user"], (name, pdf_bytes(1)))
    run_worker(s["db"])
    eng = s["engine"]
    ids = list(eng.jobs)
    eng.fail(ids[0], "tag_integrity_failed")
    eng.fail(ids[1], "tag_integrity_failed")
    run_worker(s["db"])
    db = s["db"]
    stuck = db.scalar(select(Job).where(Job.engine_job_id == ids[2]))
    stuck.submitted_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db.commit()
    items = {i["kind"]: i for i in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/attention").json()}
    # The reason in words, and a link to them (#538): two of one group, its documents filtered to failed
    assert items["failed"]["count"] == 2 and "no valid file could be made (2)" in items["failed"]["detail"]
    assert items["failed"]["link"].startswith(f"/admin/groups/{s['a'].id}/documents?status=failed&from=")
    assert items["stuck"]["count"] == 1
    assert items["stuck"]["link"] == f"/jobs/{stuck.id}" and items["stuck"]["link_label"] == "Open the document"
    # A worker lost mid-way (#315) is named as such, for super admins and the group's admins
    stuck.progress = {"stage": "worker_lost", "will_retry": True}
    db.commit()
    for client, url in (("super", f"{API}/admin/attention"), ("admin_a", f"{API}/groups/{s['a'].id}/attention")):
        items = {i["kind"]: i for i in s["c"][client].get(url).json()}
        assert items["worker_lost"]["count"] == 1 and "engine worker" in items["worker_lost"]["title"], client


def test_low_pool_warns_before_it_runs_out(setup):
    s = setup
    topup(s, s["a"].id, 200)
    user = s["c"]["user"]
    upload(user, ("big.pdf", pdf_bytes(190)))
    items = {i["kind"]: i for i in s["c"]["super"].get(f"{API}/admin/attention").json()}
    low = items.get("group_pool_low")
    assert low and low["severity"] == "warn" and "10 of 200" in low["detail"]


def test_group_services_count_people_and_own_limits(setup):
    s = setup
    _add_service(s["db"])
    _enable_everywhere(s, grant_all=False, pages=0)
    upload(s["c"]["user"], ("x.pdf", pdf_bytes(1)))
    upload(s["c"]["user2"], ("x.pdf", pdf_bytes(1)))
    a = s["c"]["admin_a"]
    a.put(f"{API}/groups/{s['a'].id}/users/{_uid(s, 'user@a.test')}/services/{EXTRA}", json={"granted": True, "cap_pages": 50})
    g = a.get(f"{API}/groups/{s['a'].id}").json()
    extra = next(x for x in g["services"] if x["key"] == EXTRA)
    std = next(x for x in g["services"] if x["key"] == "standard")
    assert extra["people_with_access"] == 1 and extra["own_limits"] == 1
    assert std["people_with_access"] >= 2 and std["own_limits"] == 0  # everyone has Standard



def test_each_item_links_to_what_its_about(setup):
    """#538: one document goes straight to it, or to its help request when one is open; help requests waiting for
    support are an item of their own, for super admins; a group's items link to the group's pages."""
    from portal.models import HelpRequest
    from tests.portal.test_help import _ask, _completed

    s = setup
    db = s["db"]
    job_id = _completed(s)
    # One failed document of the group: straight to it
    job = db.get(Job, uuid.UUID(job_id))
    job.status, job.error_code, job.finished_at = "failed", "adobe_rejected", datetime.now(timezone.utc)
    db.commit()
    items = {i["kind"]: i for i in s["c"]["super"].get(f"{API}/admin/attention").json()}
    assert "Adobe refused the PDF (1)" in items["failed"]["detail"]
    assert (items["failed"]["link"], items["failed"]["link_label"]) == (f"/jobs/{job_id}", "Open the document")
    # With a help request open about it: to the request, and the request is waiting for support
    req = _ask(s, job_id).json()["id"]
    items = {i["kind"]: i for i in s["c"]["super"].get(f"{API}/admin/attention").json()}
    assert items["failed"]["link"] == f"/admin/help/{req}"
    assert items["help_waiting"]["title"] == "1 help request waiting for support"
    assert items["help_waiting"]["link"] == f"/admin/help/{req}"
    # Answered: no longer waiting for support
    db.get(HelpRequest, uuid.UUID(req)).status = "waiting"
    db.commit()
    items = {i["kind"]: i for i in s["c"]["super"].get(f"{API}/admin/attention").json()}
    assert "help_waiting" not in items
    # The group's admins: no service-wide item, and their links are the group's
    group = {i["kind"]: i for i in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/attention").json()}
    assert "help_waiting" not in group and not any(str(i.get("link", "")).startswith(("/admin/queues", "/admin/reports",
                                                                                     "/admin/system"))
                                                   for i in group.values())



def test_acknowledging_hides_an_item_until_something_new(setup):
    """#538: acknowledged for everyone who sees that list (super admins', or the group's admins'), audited; it comes
    back counting only what's new; it can be shown again. Live problems can't be acknowledged."""
    from portal.models import AuditEvent

    s = setup
    db = s["db"]
    topup(s, s["a"].id, 100)

    def fail(name):
        job_id = upload(s["c"]["user"], (name, pdf_bytes(1))).json()[0]["job"]["id"]
        job = db.get(Job, uuid.UUID(job_id))
        job.status, job.error_code, job.finished_at = "failed", "engine_rejected", datetime.now(timezone.utc)
        db.commit()

    def kinds(client, url):
        return {i["kind"]: i for i in s["c"][client].get(url).json()}

    root, group = f"{API}/admin/attention", f"{API}/groups/{s['a'].id}/attention"
    fail("one.pdf")
    item = kinds("super", root)["failed"]
    assert item["ackable"] and item["key"] == "failed"
    r = s["c"]["super"].post(f"{root}/acknowledge", json={"key": "failed"})
    assert r.status_code == 200 and "failed" not in {i["kind"] for i in r.json()}
    assert "failed" not in kinds("super", root)
    [ack] = s["c"]["super"].get(f"{root}/acknowledged").json()
    assert ack["acked_by"] == "root@admin.test" and ack["count"] == 1 and ack["still"] == 1
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "attention.acknowledged")).one().details["key"] == "failed"
    # The group's admins have their own list: still there for them, until they acknowledge it
    assert "failed" in kinds("admin_a", group)
    assert s["c"]["admin_a"].post(f"{group}/acknowledge", json={"key": "failed"}).status_code == 200
    assert "failed" not in kinds("admin_a", group)
    assert s["c"]["admin_b"].post(f"{API}/groups/{s['a'].id}/attention/acknowledge",
                                  json={"key": "failed"}).status_code == 404  # not their group (as for its other pages)
    # Something new: back, counting only the new one
    fail("two.pdf")
    back = kinds("super", root)["failed"]
    assert back["count"] == 1 and back["earlier"] == 1 and back["title"].startswith("1 document failed")
    # Shown again in full
    assert s["c"]["super"].delete(f"{root}/acknowledge", params={"key": "failed"}).status_code == 200
    assert kinds("super", root)["failed"]["count"] == 2
    # A live problem can't be: it goes when it's fixed
    upload(s["c"]["user"], ("big.pdf", pdf_bytes(95)))  # the pool runs low
    low = kinds("super", root)["group_pool_low"]
    assert not low.get("ackable")
    assert s["c"]["super"].post(f"{root}/acknowledge", json={"key": low["key"]}).status_code == 409
