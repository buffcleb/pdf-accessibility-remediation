"""Subgroups (#431). Phase 1: one level deep; a parent's admins manage its subgroups and see their people,
documents, help requests and reports with the parent's; a subgroup's admins see only their subgroup. Phase 2: a
subgroup's documents use its parent's pages, up to a limit of its own that the parent's admins set."""
from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portal import pool_alerts, quota
from portal.models import AuditEvent, Group, GroupDomain, GroupEmail, GroupService, Job, MailOutbox, QuotaLedger, User
from tests.portal.conftest import login, make_group, make_user
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, setup, topup, upload  # noqa: F401
from tests.portal.test_rls import PROBE, probe  # noqa: F401


@pytest.fixture
def family(setup, make_client):
    """Campus A with a subgroup, Nursing (its own admin and member), beside Campus B."""
    s, db = setup, setup["db"]
    sub = make_group(db, "Campus A Nursing", parent_id=s["a"].id)
    make_user(db, "nurse-admin@nursing.test", sub, admin_of=sub)
    make_user(db, "nurse@nursing.test", sub)
    for name, email in [("sub_admin", "nurse-admin@nursing.test"), ("nurse", "nurse@nursing.test")]:
        s["c"][name] = make_client()
        login(s["c"][name], email)
    s["sub"] = sub
    return s


def _job(s, who, name="a.pdf"):
    r = upload(s["c"][who], (name, pdf_bytes(1)))
    assert r.status_code == 200 and r.json()[0]["accepted"], r.text
    return r.json()[0]["job"]["id"]


def test_one_level_only_and_never_its_own_parent(family):
    db = family["db"]
    with pytest.raises(DBAPIError):  # a subgroup can't have subgroups
        make_group(db, "Too deep", parent_id=family["sub"].id)
    db.rollback()
    with pytest.raises(DBAPIError):  # a group with subgroups can't go under another
        db.execute(text("update groups set parent_id = :b where id = :a"), {"a": family["a"].id, "b": family["b"].id})
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("update groups set parent_id = id where id = :b"), {"b": family["b"].id})
    db.rollback()


def test_the_parents_admin_manages_the_subgroup(family):
    s = family
    admin = s["c"]["admin_a"]
    assert {g["name"] for g in admin.get(f"{API}/groups").json()} == {"Campus A", "Campus A Nursing"}
    group = admin.get(f"{API}/groups/{s['a'].id}").json()
    assert [g["name"] for g in group["subgroups"]] == ["Campus A Nursing"] and group["parent"] is None
    sub = admin.get(f"{API}/groups/{s['sub'].id}").json()
    assert sub["parent"]["name"] == "Campus A"
    # The parent's people include the subgroup's, each with their group
    people = {p["email"]: p["group_name"] for p in admin.get(f"{API}/groups/{s['a'].id}/people").json()["items"]}
    assert people["nurse@nursing.test"] == "Campus A Nursing" and people["user@a.test"] == "Campus A"
    nurse = next(p for p in admin.get(f"{API}/groups/{s['a'].id}/people").json()["items"]
                 if p["email"] == "nurse@nursing.test")
    r = admin.patch(f"{API}/groups/{s['a'].id}/users/{nurse['id']}", json={"status": "disabled"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"


def test_the_parents_lists_include_the_subgroups_documents_and_help(family):
    s = family
    topup(s, s["a"].id, 10)
    mine, theirs = _job(s, "user", "parent.pdf"), _job(s, "nurse", "nursing.pdf")
    admin = s["c"]["admin_a"]
    docs = {d["filename"] for d in admin.get(f"{API}/groups/{s['a'].id}/documents").json()["items"]}
    assert docs == {"parent.pdf", "nursing.pdf"}
    assert admin.get(f"{API}/jobs/{theirs}").status_code == 200
    from portal.models import HelpRequest, Job
    job = s["db"].get(Job, __import__("uuid").UUID(theirs))
    s["db"].add(HelpRequest(job_id=job.id, filename=job.filename, user_id=job.user_id, group_id=job.group_id, kind="other"))
    s["db"].commit()
    assert admin.get(f"{API}/groups/{s['a'].id}/help/list").json()["total"] == 1
    usage = admin.get(f"{API}/groups/{s['a'].id}/reports/usage.csv")
    assert usage.status_code == 200 and "nurse@nursing.test" in usage.text and "user@a.test" in usage.text
    # The subgroup's admin sees the subgroup's, and nothing of the parent's or another group's
    sub_admin = s["c"]["sub_admin"]
    assert {d["filename"] for d in sub_admin.get(f"{API}/groups/{s['sub'].id}/documents").json()["items"]} \
        == {"nursing.pdf"}
    assert sub_admin.get(f"{API}/jobs/{mine}").status_code == 404
    for gid in (s["a"].id, s["b"].id):
        assert sub_admin.get(f"{API}/groups/{gid}").status_code == 404
        assert sub_admin.get(f"{API}/groups/{gid}/documents").status_code == 404
        assert sub_admin.get(f"{API}/groups/{gid}/people").status_code == 404
    assert {g["name"] for g in sub_admin.get(f"{API}/groups").json()} == {"Campus A Nursing"}
    # Another group's admin sees none of it
    assert s["c"]["admin_b"].get(f"{API}/jobs/{theirs}").status_code == 404
    assert s["c"]["admin_b"].get(f"{API}/groups/{s['sub'].id}").status_code == 404


def test_row_level_security_follows(family, probe):
    """With the API's checks out of the way, the database itself shows a parent's admin the subgroup's rows, and a
    subgroup's admin none of the parent's."""
    s = family
    topup(s, s["a"].id, 10)
    _job(s, "user", "parent.pdf")
    _job(s, "nurse", "nursing.pdf")
    count = {"all": "select count(*) from jobs", "users": "select count(*) from users where email like '%nursing.test'"}
    assert s["c"]["admin_a"].post(PROBE, json=count).json() == {"all": 2, "users": 2}
    assert s["c"]["sub_admin"].post(PROBE, json=count).json() == {"all": 1, "users": 2}
    assert s["c"]["admin_b"].post(PROBE, json=count).json() == {"all": 0, "users": 0}


def test_making_subgroups(family):
    s = family
    r = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/subgroups", json={"name": "Campus A Library"})
    assert r.status_code == 201 and r.json()["parent"]["name"] == "Campus A"
    r = s["c"]["sub_admin"].post(f"{API}/groups/{s['sub'].id}/subgroups", json={"name": "Deeper"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "subgroup_depth"
    assert s["c"]["admin_b"].post(f"{API}/groups/{s['a'].id}/subgroups", json={"name": "Not mine"}).status_code == 404
    assert s["c"]["nurse"].post(f"{API}/groups/{s['a'].id}/subgroups", json={"name": "Nope"}).status_code in (403, 404)
    r = s["c"]["super"].post(f"{API}/admin/groups", json={"name": "Campus B Law", "parent_id": str(s["b"].id)})
    assert r.status_code in (200, 201) and r.json()["parent"]["name"] == "Campus B"
    # A local-only parent's subgroup is local-only too
    s["db"].execute(text("update groups set local_only = true where id = :b"), {"b": s["b"].id})
    s["db"].commit()
    r = s["c"]["super"].post(f"{API}/admin/groups", json={"name": "Campus B Med", "parent_id": str(s["b"].id)})
    assert r.json()["local_only"] is True


def test_a_subgroup_stays_local_only_while_its_parent_is(family):
    s, db = family, family["db"]
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"local_only": True})
    assert r.status_code == 200 and r.json()["local_only"] is True
    db.expire_all()
    assert db.get(Group, s["sub"].id).local_only is True  # carried down
    for who, url in [("sub_admin", f"{API}/groups/{s['sub'].id}"), ("super", f"{API}/admin/groups/{s['sub'].id}")]:
        r = s["c"][who].patch(url, json={"local_only": False})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "parent_local_only"
    # Phase 3: once the parent lets documents out, a subgroup that follows it does too; one that set local-only
    # itself keeps it
    s["c"]["super"].patch(f"{API}/admin/groups/{s['a'].id}", json={"local_only": False})
    db.expire_all()
    assert db.get(Group, s["sub"].id).local_only is False
    r = s["c"]["sub_admin"].patch(f"{API}/groups/{s['sub'].id}", json={"local_only": True})
    assert r.status_code == 200 and "local_only" not in r.json()["inherits"]
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"local_only": True})
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"local_only": False})
    db.expire_all()
    assert db.get(Group, s["sub"].id).local_only is True


def test_someone_waiting_in_a_subgroup_reaches_both_groups_admins(family):
    s, db = family, family["db"]
    db.execute(text("update groups set require_approval = true where id = :g"), {"g": s["sub"].id})
    db.commit()
    from portal import approval
    from portal.models import User
    from sqlalchemy import select
    sub = db.get(Group, s["sub"].id)
    assert approval._admins_of(db, sub) == ["admin@a.test", "nurse-admin@nursing.test"]
    waiting = make_user(db, "new@nursing.test", sub)
    db.execute(text("update users set approval = 'pending' where id = :u"), {"u": waiting.id})
    db.commit()
    assert s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()["pending_members"] == 1
    r = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/users/{waiting.id}/approve")
    assert r.status_code == 200 and db.scalar(select(User.approval).where(User.id == waiting.id)) == "approved"


# --- phase 2: pages ------------------------------------------------------------------------------

def _std(db, group_id) -> GroupService:
    db.expire_all()
    return db.scalar(select(GroupService).where(GroupService.group_id == group_id, GroupService.service_key == "standard"))


def _cap(client, s, cap):
    return client.patch(f"{API}/groups/{s['sub'].id}/services/standard", json={"cap_pages": cap})


def test_a_subgroup_uses_its_parents_pages(family):
    s, db = family, family["db"]
    r = topup(s, s["sub"].id, 10)  # it has no pool of its own
    assert r.status_code == 422 and r.json()["detail"]["error"] == "subgroup_pool"
    topup(s, s["a"].id, 10)
    job_id = _job(s, "nurse")
    pool, sub = _std(db, s["a"].id), _std(db, s["sub"].id)
    assert (pool.reserved_pages, sub.reserved_pages, sub.pool_pages) == (1, 1, 0)
    entry = db.scalar(select(QuotaLedger).where(QuotaLedger.job_id == uuid.UUID(job_id)))
    assert entry.group_id == s["a"].id and entry.subgroup_id == s["sub"].id and entry.pool_after == 10
    quota.commit(db, db.get(Job, uuid.UUID(job_id)))
    db.commit()
    pool, sub = _std(db, s["a"].id), _std(db, s["sub"].id)
    assert (pool.used_pages, pool.reserved_pages, sub.used_pages, sub.reserved_pages) == (1, 0, 1, 0)
    # The balances: the parent's pool, and the subgroup's share of it
    bal = s["c"]["sub_admin"].get(f"{API}/groups/{s['sub'].id}").json()["balance"]
    assert bal == {"pool_pages": 10, "used_pages": 1, "reserved_pages": 0, "remaining_pages": 9, "cap_pages": None,
                   "shared": True}
    assert s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()["balance"]["remaining_pages"] == 9
    # The parent running out stops the subgroup too, named as the parent's pages
    pool.used_pages = 10
    db.commit()
    err = upload(s["c"]["nurse"], ("b.pdf", pdf_bytes(1))).json()[0]["error"]
    assert err["scope"] == "group" and err["message"].startswith("Campus A, whose pages your group uses, has 0")


def test_the_subgroups_limit_is_set_by_its_parents_admins(family):
    s = family
    topup(s, s["a"].id, 100)
    assert _cap(s["c"]["sub_admin"], s, 4).status_code == 403  # not the subgroup's own admins
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/services/standard", json={"cap_pages": 4})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "not_subgroup"
    r = _cap(s["c"]["admin_a"], s, 4)
    assert r.status_code == 200 and r.json()["balance"]["cap_pages"] == 4 and r.json()["balance"]["remaining_pages"] == 4
    assert _cap(s["c"]["admin_b"], s, 50).status_code == 404
    assert upload(s["c"]["nurse"], ("a.pdf", pdf_bytes(3))).json()[0]["accepted"]
    err = upload(s["c"]["nurse"], ("b.pdf", pdf_bytes(3))).json()[0]["error"]
    assert err["scope"] == "subgroup" and err["remaining_pages"] == 1
    assert err["message"].startswith("Your group has 1 Standard page left of its limit and this document needs 3.")
    assert upload(s["c"]["user"], ("c.pdf", pdf_bytes(3))).json()[0]["accepted"]  # the parent's people aren't limited
    # The subgroup's ledger: its limit and its documents; the parent's has those and its own
    kinds = [e["kind"] for e in s["c"]["sub_admin"].get(f"{API}/groups/{s['sub'].id}/ledger").json()]
    assert kinds == ["reserve", "cap"]
    kinds = [e["kind"] for e in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/ledger").json()]
    assert kinds == ["reserve", "reserve", "cap", "topup"]
    # Taking the limit off: whatever the parent has left
    r = _cap(s["c"]["super"], s, None)
    assert r.json()["balance"]["cap_pages"] is None and r.json()["balance"]["remaining_pages"] == 94


def test_parallel_uploads_overdraw_neither_the_limit_nor_the_pool(family):
    """The parent's 30 pages, the subgroup limited to 20: of ten 10-page documents at once, five from each group,
    three go through, at most two of them the subgroup's."""
    s, db = family, family["db"]
    topup(s, s["a"].id, 30)
    _cap(s["c"]["admin_a"], s, 20)
    ids = {e: db.scalar(select(User.id).where(User.email == e)) for e in ("user@a.test", "nurse@nursing.test")}
    ok, refused = [], []
    barrier = threading.Barrier(10)

    def attempt(email):
        with Session(db.get_bind(), expire_on_commit=False) as sess:
            user = sess.get(User, ids[email])
            barrier.wait()
            try:
                quota.reserve(sess, user, 10, uuid.uuid4())
                sess.commit()
                ok.append(email)
            except quota.OverQuota as e:
                sess.rollback()
                refused.append(e.detail["scope"])

    threads = [threading.Thread(target=attempt, args=(e,)) for e in [*ids] * 5]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    nursing = ok.count("nurse@nursing.test")
    assert len(ok) == 3 and nursing <= 2 and len(refused) == 7
    assert _std(db, s["a"].id).reserved_pages == 30 and _std(db, s["sub"].id).reserved_pages == 10 * nursing


def test_row_level_security_shows_a_subgroups_admin_its_entries(family, probe):
    s = family
    topup(s, s["a"].id, 10)
    _job(s, "user", "parent.pdf")
    _job(s, "nurse", "nursing.pdf")
    count = {"n": "select count(*) from quota_ledger"}
    assert s["c"]["admin_a"].post(PROBE, json=count).json() == {"n": 3}  # the top-up and both documents
    assert s["c"]["sub_admin"].post(PROBE, json=count).json() == {"n": 1}
    assert s["c"]["nurse"].post(PROBE, json=count).json() == {"n": 1}
    assert s["c"]["admin_b"].post(PROBE, json=count).json() == {"n": 0}


def test_a_low_limit_tells_the_subgroups_and_the_parents_admins(family):
    s, db = family, family["db"]
    topup(s, s["a"].id, 1000)
    _cap(s["c"]["admin_a"], s, 100)
    assert pool_alerts.check(db) == []
    db.commit()
    sub = _std(db, s["sub"].id)
    sub.used_pages = 90  # 10 of its 100 left; the parent still has plenty
    db.commit()
    assert len(pool_alerts.check(db)) == 1
    db.commit()
    db.expire_all()
    mailed = sorted((m.to_addr, m.subject) for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "alert")))
    assert mailed == [("admin@a.test", "Campus A Nursing: Standard pages running low"),
                      ("nurse-admin@nursing.test", "Campus A Nursing: Standard pages running low")]


# --- phase 3: settings, sign-in and moving people --------------------------------------------------

def _sub(s):
    return s["c"]["sub_admin"].get(f"{API}/groups/{s['sub'].id}").json()


def test_a_subgroup_follows_its_parents_settings_until_it_sets_its_own(family):
    s = family
    admin, sub_admin = s["c"]["admin_a"], s["c"]["sub_admin"]
    assert _sub(s)["inherits"] == ["ai", "local_only", "require_approval", "ai_describe", "show_external",
                                   "over_quota_message"]
    admin.patch(f"{API}/groups/{s['a'].id}", json={"show_external": True, "require_approval": True,
                                                    "ai_describe": False})
    sub = _sub(s)
    assert (sub["show_external"], sub["require_approval"], sub["ai_describe"]) == (True, True, False)
    # Set by the subgroup: its own from then on, whatever the parent does
    r = sub_admin.patch(f"{API}/groups/{s['sub'].id}", json={"show_external": False})
    assert r.status_code == 200 and "show_external" not in r.json()["inherits"]
    admin.patch(f"{API}/groups/{s['a'].id}", json={"show_external": False})
    admin.patch(f"{API}/groups/{s['a'].id}", json={"show_external": True})
    assert _sub(s)["show_external"] is False
    # Back to the parent's: its value now, and its changes from then on
    r = sub_admin.patch(f"{API}/groups/{s['sub'].id}", json={"inherit": ["show_external"]})
    assert r.json()["show_external"] is True and "show_external" in r.json()["inherits"]
    r = sub_admin.patch(f"{API}/groups/{s['sub'].id}", json={"inherit": ["show_external"], "show_external": True})
    assert r.status_code == 422
    r = sub_admin.patch(f"{API}/groups/{s['sub'].id}", json={"inherit": ["nonsense"]})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unknown_setting"
    r = admin.patch(f"{API}/groups/{s['a'].id}", json={"inherit": ["show_external"]})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "not_subgroup"
    # The over-quota message follows too (a super admin's to set), and the refusal shows it
    s["c"]["super"].patch(f"{API}/admin/groups/{s['a'].id}", json={"over_quota_message": "Ask the library."})
    assert _sub(s)["over_quota_message"] == "Ask the library."


def test_a_new_subgroup_starts_with_its_parents_settings_and_services(family):
    s = family
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"show_external": True, "pdfua_choice": False})
    s["c"]["super"].patch(f"{API}/admin/groups/{s['a'].id}", json={"default_user_cap": 25})
    r = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/subgroups", json={"name": "Campus A Library"})
    assert r.status_code == 201
    g = r.json()
    assert g["show_external"] is True and g["default_user_cap"] == 25 and len(g["inherits"]) == 6


def test_moving_people_between_a_group_and_its_subgroups(family):
    s, db = family, family["db"]
    admin, sub_admin = s["c"]["admin_a"], s["c"]["sub_admin"]
    ids = {e: db.scalar(select(User.id).where(User.email == e))
           for e in ("user@a.test", "nurse@nursing.test", "nurse-admin@nursing.test", "admin@a.test")}
    url = lambda gid, who: f"{API}/groups/{gid}/users/{ids[who]}"  # noqa: E731
    # The parent's admin moves people both ways
    r = admin.patch(url(s["a"].id, "user@a.test"), json={"group_id": str(s["sub"].id)})
    assert r.status_code == 200 and r.json()["group_id"] == str(s["sub"].id)
    r = admin.patch(url(s["a"].id, "user@a.test"), json={"group_id": str(s["a"].id)})
    assert r.status_code == 200 and r.json()["group_id"] == str(s["a"].id)
    # The subgroup's admin doesn't run the parent: they can't move their people out to it
    r = sub_admin.patch(url(s["sub"].id, "nurse@nursing.test"), json={"group_id": str(s["a"].id)})
    assert r.status_code == 403
    # Nobody but a super admin moves people out of the family
    r = admin.patch(url(s["a"].id, "nurse@nursing.test"), json={"group_id": str(s["b"].id)})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "outside_family"
    r = admin.patch(url(s["a"].id, "admin@a.test"), json={"group_id": str(s["sub"].id)})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "self_move"
    # A subgroup admin moved to the parent leaves their admin rights behind
    r = admin.patch(url(s["a"].id, "nurse-admin@nursing.test"), json={"group_id": str(s["a"].id)})
    assert r.status_code == 200
    assert sub_admin.get(f"{API}/groups/{s['sub'].id}").status_code in (403, 404)
    events = s["db"].scalars(select(AuditEvent).where(AuditEvent.action == "user.updated")).all()
    assert any("group_id" in (e.details or {}) for e in events)


def test_signing_in_matches_a_subgroups_address_or_domain_first(family):
    s, db = family, family["db"]
    from portal import identity
    db.add(GroupEmail(email="dean@a.test", group_id=s["sub"].id))
    db.add(GroupDomain(domain="nursing.a.test", group_id=s["sub"].id))
    db.commit()
    assert identity.eligibility(db, "dean@a.test").group.id == s["sub"].id  # listed on the subgroup
    assert identity.eligibility(db, "new@nursing.a.test").group.id == s["sub"].id  # the subgroup's domain
    assert identity.eligibility(db, "new@a.test").group.id == s["a"].id  # the rest of the parent's domain


# --- phase 4: screens and reports ----------------------------------------------------------------

def test_the_groups_list_is_a_tree(family):
    s = family
    s["c"]["super"].post(f"{API}/groups/{s['b'].id}/subgroups", json={"name": "Aardvark Unit"})
    names = [g["name"] for g in s["c"]["super"].get(f"{API}/groups/browse").json()["items"]]
    # Each parent, then its subgroups under it, whatever their own names
    assert names.index("Campus A") < names.index("Campus A Nursing") < names.index("Campus B") < names.index("Aardvark Unit")
    assert [g["name"] for g in s["c"]["admin_a"].get(f"{API}/groups/browse").json()["items"]] == ["Campus A", "Campus A Nursing"]


def test_a_parents_report_can_be_narrowed_to_a_subgroup(family):
    s = family
    topup(s, s["a"].id, 10)
    _job(s, "user", "parent.pdf")
    _job(s, "nurse", "nursing.pdf")
    admin = s["c"]["admin_a"]
    r = admin.get(f"{API}/reports/documents?group_id={s['a'].id}").json()
    assert r["total_rows"] == 2
    group_filter = r["filters"][0]
    assert group_filter["key"] == "in_group"
    assert [o["label"] for o in group_filter["options"]] == ["Campus A", "Campus A Nursing (subgroup)"]
    r = admin.get(f"{API}/reports/documents?group_id={s['a'].id}&in_group={s['sub'].id}").json()
    assert r["total_rows"] == 1
    # Not a way out of the report's groups, and not offered for one group
    r = admin.get(f"{API}/reports/documents?group_id={s['a'].id}&in_group={s['b'].id}").json()
    assert r["total_rows"] == 2
    r = s["c"]["sub_admin"].get(f"{API}/reports/documents?group_id={s['sub'].id}").json()
    assert r["total_rows"] == 1 and not any(f["key"] == "in_group" for f in r["filters"])


def test_why_cant_this_person_use_it_names_the_parents_pool(family):
    s = family
    topup(s, s["a"].id, 10)
    _cap(s["c"]["admin_a"], s, 4)
    check = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/access-check",
                                  params={"email": "nurse@nursing.test", "service": "standard"}).json()
    pages = next(st for st in check["steps"] if st["key"] == "group_pages")
    assert pages["ok"] and pages["label"] == "Pages left for Campus A Nursing, from Campus A's pool"
    assert pages["detail"].startswith("4 of 4 left")
