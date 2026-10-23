"""#514 part 2: People, a group's People and Groups, a page at a time on the server, with search, filters and sort, in
the same shape as /documents ({items, total, page, page_size})."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from portal.models import RoleAssignment, User
from tests.portal.conftest import make_group, make_user
from tests.portal.test_jobs import API, fake_engine, setup  # noqa: F401

PEOPLE = f"{API}/admin/people"


def _emails(body):
    return [u["email"] for u in body["items"]]


def _crowd(s, n=30):
    for i in range(n):
        make_user(s["db"], f"member{i:02d}@a.test", s["a"])


def test_people_a_page_at_a_time_in_one_order(setup):
    s = setup
    _crowd(s)
    sup = s["c"]["super"]
    first = sup.get(PEOPLE, params={"page_size": 25}).json()
    assert first["total"] >= 35 and first["page"] == 1 and first["page_size"] == 25 and len(first["items"]) == 25
    second = sup.get(PEOPLE, params={"page_size": 25, "page": 2}).json()
    seen = _emails(first) + _emails(second)
    assert len(seen) == first["total"] and len(set(seen)) == len(seen) and seen == sorted(seen)
    assert _emails(sup.get(PEOPLE, params={"sort": "-email"}).json())[0] == max(seen)
    assert sup.get(PEOPLE, params={"sort": "shoe_size"}).status_code == 422
    # Super admins only
    assert s["c"]["admin_a"].get(PEOPLE).status_code == 403


def test_people_search_and_filters(setup):
    s = setup
    _crowd(s, 3)
    db = s["db"]
    sup = s["c"]["super"]
    assert _emails(sup.get(PEOPLE, params={"q": "MEMBER01"}).json()) == ["member01@a.test"]
    assert sup.get(PEOPLE, params={"q": "_"}).json()["total"] == 0  # "_" is a letter, not a wildcard
    assert set(_emails(sup.get(PEOPLE, params={"group_id": str(s["b"].id)}).json())) == {"admin@b.test"}
    assert _emails(sup.get(PEOPLE, params={"role": "super_admin"}).json()) == ["root@admin.test"]
    assert set(_emails(sup.get(PEOPLE, params={"role": "group_admin"}).json())) == {"admin@a.test", "admin@b.test"}
    members = _emails(sup.get(PEOPLE, params={"role": "member", "page_size": 200}).json())
    assert "member00@a.test" in members and "admin@a.test" not in members and "root@admin.test" not in members
    m1 = db.scalar(select(User).where(User.email == "member01@a.test"))
    m2 = db.scalar(select(User).where(User.email == "member02@a.test"))
    m1.status = "disabled"
    m2.approval = "pending"
    db.commit()
    assert _emails(sup.get(PEOPLE, params={"status": "disabled"}).json()) == ["member01@a.test"]
    assert _emails(sup.get(PEOPLE, params={"status": "pending"}).json()) == ["member02@a.test"]
    m1.removed_at = datetime.now(timezone.utc)
    db.commit()
    assert _emails(sup.get(PEOPLE, params={"status": "removed"}).json()) == ["member01@a.test"]
    assert sup.get(PEOPLE, params={"status": "disabled"}).json()["total"] == 0  # removed is its own status
    assert sup.get(PEOPLE, params={"status": "gone"}).status_code == 422


def test_a_groups_people_for_its_admins(setup):
    s = setup
    _crowd(s)
    db = s["db"]
    url = f"{API}/groups/{s['a'].id}/people"
    admin = s["c"]["admin_a"]
    first = admin.get(url).json()
    assert first["total"] >= 31 and len(first["items"]) == 25
    every = _emails(first) + _emails(admin.get(url, params={"page": 2}).json())
    assert all(e.endswith("@a.test") for e in every) and every == sorted(every)
    # Waiting for approval: listed apart, not in this list
    waiting = db.scalar(select(User).where(User.email == "member03@a.test"))
    waiting.approval = "pending"
    db.commit()
    assert "member03@a.test" not in _emails(admin.get(url, params={"page_size": 200}).json())
    assert _emails(admin.get(url, params={"status": "admin"}).json()) == ["admin@a.test"]
    assert _emails(admin.get(url, params={"q": "member04"}).json()) == ["member04@a.test"]
    # Who has a service: with everyone given it, everyone has it
    has = admin.get(url, params={"service": "standard", "has": "true", "page_size": 200}).json()["total"]
    assert has == admin.get(url, params={"page_size": 200}).json()["total"]
    assert admin.get(url, params={"service": "standard", "has": "false"}).json()["total"] == 0
    assert admin.get(url, params={"service": "nope", "has": "true"}).status_code == 422
    # Another group's admin, and a member, get nothing
    assert s["c"]["admin_b"].get(url).status_code in (403, 404)
    assert s["c"]["user"].get(url).status_code in (403, 404)


def test_groups_a_page_at_a_time(setup):
    s = setup
    db = s["db"]
    for i in range(27):
        make_group(db, f"Campus {i:02d}", f"c{i:02d}.test")
    url = f"{API}/groups/browse"
    sup = s["c"]["super"]
    first = sup.get(url).json()
    assert first["total"] == 29 and len(first["items"]) == 25
    names = [g["name"] for g in first["items"]] + [g["name"] for g in sup.get(url, params={"page": 2}).json()["items"]]
    assert names == sorted(names, key=str.lower) and len(set(names)) == 29
    assert [g["name"] for g in sup.get(url, params={"q": "c05.test"}).json()["items"]] == ["Campus 05"]  # by domain
    assert [g["name"] for g in sup.get(url, params={"q": "campus b"}).json()["items"]] == ["Campus B"]
    assert sup.get(url, params={"status": "disabled"}).json()["total"] == 0
    # A group admin: their own groups only
    mine = s["c"]["admin_a"].get(url).json()
    assert [g["name"] for g in mine["items"]] == ["Campus A"] and mine["total"] == 1
    assert s["c"]["user"].get(url).json()["total"] == 0
    # (the role table is what makes someone a group's admin)
    assert db.scalar(select(RoleAssignment).where(RoleAssignment.role == "group_admin", RoleAssignment.group_id == s["a"].id))
