"""#95: people allowed into a group by their address, without their whole domain."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from portal.models import AuditEvent
from tests.portal.conftest import login, make_group, make_user

API = "/api/v1"
CODE = f"{API}/auth/code"


@pytest.fixture
def world(db, make_client):
    a = make_group(db, "Campus A", "a.test")
    guests = make_group(db, "Individuals")  # no domains: only listed addresses
    make_user(db, "root@admin.test", None, role="super_admin")
    sup = make_client()
    login(sup, "root@admin.test")
    return {"a": a, "guests": guests, "super": sup, "db": db}


def _add(w, group, email, **kw):
    return w["super"].post(f"{API}/admin/groups/{w[group].id}/emails", json={"email": email, **kw})


def test_a_listed_address_signs_in_without_its_domain(world, make_client):
    r = _add(world, "guests", "Pat.Smith@Gmail.Test", note="Visiting researcher")
    assert r.status_code == 201, r.text
    listed = r.json()["emails"]
    assert listed[0]["email"] == "pat.smith@gmail.test" and listed[0]["note"] == "Visiting researcher"
    assert listed[0]["added_by"] == "root@admin.test" and not listed[0]["has_account"]
    body = login(make_client(), "pat.smith@gmail.test")
    assert body["user"]["group_id"] == str(world["guests"].id)
    # Someone else at the same domain still can't
    r = make_client().post(CODE, json={"email": "someone@gmail.test"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "not_provisioned"
    assert world["super"].get(f"{API}/admin/groups/{world['guests'].id}").json()["emails"][0]["has_account"]
    actions = [e.action for e in world["db"].scalars(select(AuditEvent).where(AuditEvent.action.like("group.email%")))]
    assert actions == ["group.email_added"]


def test_a_listed_address_wins_over_its_domain(world, make_client):
    _add(world, "guests", "contractor@a.test")
    assert login(make_client(), "contractor@a.test")["user"]["group_id"] == str(world["guests"].id)
    assert login(make_client(), "staff@a.test")["user"]["group_id"] == str(world["a"].id)


def test_an_address_is_on_one_group_at_a_time(world):
    _add(world, "guests", "pat@gmail.test")
    r = _add(world, "a", "pat@gmail.test")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "email_taken" and "Individuals" in r.json()["detail"]["message"]
    r = _add(world, "a", "pat@gmail.test", move=True)
    assert r.status_code == 201 and [e["email"] for e in r.json()["emails"]] == ["pat@gmail.test"]
    assert world["super"].get(f"{API}/admin/groups/{world['guests'].id}").json()["emails"] == []


def test_someone_with_an_account_elsewhere_is_moved_not_listed(world, make_client):
    login(make_client(), "staff@a.test")
    r = _add(world, "guests", "staff@a.test")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "has_account"


def test_removing_the_address_keeps_the_account(world, make_client):
    _add(world, "guests", "pat@gmail.test")
    login(make_client(), "pat@gmail.test")
    r = world["super"].delete(f"{API}/admin/groups/{world['guests'].id}/emails/pat@gmail.test")
    assert r.status_code == 200 and r.json()["emails"] == []
    assert make_client().post(CODE, json={"email": "pat@gmail.test"}).status_code == 202  # still has an account
    assert world["super"].delete(f"{API}/admin/groups/{world['guests'].id}/emails/pat@gmail.test").status_code == 404


def test_only_super_admins_manage_addresses(world, make_client):
    make_user(world["db"], "admin@a.test", world["a"], admin_of=world["a"])
    admin = make_client()
    login(admin, "admin@a.test")
    assert admin.post(f"{API}/admin/groups/{world['a'].id}/emails", json={"email": "x@gmail.test"}).status_code == 403


def test_a_listed_address_can_be_made_group_admin(world):
    _add(world, "guests", "lead@gmail.test")
    r = world["super"].post(f"{API}/admin/groups/{world['guests'].id}/admins", json={"email": "lead@gmail.test"})
    assert r.status_code == 201, r.text
    r = world["super"].post(f"{API}/admin/groups/{world['guests'].id}/admins", json={"email": "other@gmail.test"})
    assert r.status_code == 422


def test_public_email_domains_need_confirming(world):
    r = world["super"].post(f"{API}/admin/groups/{world['a'].id}/domains", json={"domain": "Gmail.com"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "public_domain"
    assert "People allowed by address" in r.json()["detail"]["message"]
    r = world["super"].post(f"{API}/admin/groups/{world['a'].id}/domains", json={"domain": "gmail.com", "public_ok": True})
    assert r.status_code == 201 and "gmail.com" in r.json()["domains"]
