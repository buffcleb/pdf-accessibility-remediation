from __future__ import annotations

import uuid

import pytest

from tests.portal.conftest import login, make_group, make_user

API = "/api/v1"


@pytest.fixture
def world(db, make_client):
    """Two groups, each with an admin and a user, plus a super admin. Returns signed-in clients."""
    a = make_group(db, "Campus A", "a.test")
    b = make_group(db, "Campus B", "b.test")
    make_user(db, "root@admin.test", None, role="super_admin")
    make_user(db, "admin@a.test", a, admin_of=a)
    make_user(db, "admin@b.test", b, admin_of=b)
    clients = {}
    for name, email in [("super", "root@admin.test"), ("admin_a", "admin@a.test"), ("admin_b", "admin@b.test"),
                        ("user_a", "user@a.test"), ("user_b", "user@b.test")]:
        c = make_client()
        login(c, email)
        clients[name] = c
    clients["anon"] = make_client()
    users = {e: u for e, u in ((u["email"], u) for u in clients["super"].get(f"{API}/admin/users").json())}
    return {"a": a, "b": b, "c": clients, "users": users}


# Every protected endpoint and who may call it. "A" = admin of group A.
def _matrix(w):
    a, b = w["a"].id, w["b"].id
    ua = w["users"]["user@a.test"]["id"]
    return [
        ("get", "/me", None, {"super", "admin_a", "admin_b", "user_a", "user_b"}),
        ("get", "/groups", None, {"super", "admin_a", "admin_b", "user_a", "user_b"}),
        ("get", f"/groups/{a}", None, {"super", "admin_a"}),
        ("get", f"/groups/{a}/users", None, {"super", "admin_a"}),
        ("patch", f"/groups/{a}/users/{ua}", {"cap_pages": 10}, {"super", "admin_a"}),
        ("get", f"/groups/{a}/audit", None, {"super", "admin_a"}),
        ("get", "/admin/groups", None, {"super"}),
        ("post", "/admin/groups", {"name": f"G{uuid.uuid4().hex[:6]}"}, {"super"}),
        ("get", f"/admin/groups/{b}", None, {"super"}),
        ("patch", f"/admin/groups/{b}", {"default_user_cap": 5}, {"super"}),
        ("post", f"/admin/groups/{b}/domains", {"domain": "b2.test"}, {"super"}),
        ("post", f"/admin/groups/{b}/emails", {"email": "guest@gmail.test"}, {"super"}),
        ("get", f"/admin/groups/{b}/admins", None, {"super"}),
        ("get", "/admin/users", None, {"super"}),
        ("patch", f"/admin/users/{ua}", {"name": "User A"}, {"super"}),
        ("get", "/admin/settings", None, {"super"}),
        ("patch", "/admin/settings", {"max_file_mb": 50}, {"super"}),
        ("get", "/admin/audit", None, {"super"}),
    ]


def test_role_endpoint_matrix(world):
    for method, path, body, allowed in _matrix(world):
        for who, client in world["c"].items():
            kwargs = {"json": body} if body is not None else {}
            r = getattr(client, method)(API + path, **kwargs)
            if who == "anon":
                assert r.status_code == 401, (who, method, path, r.status_code)
            elif who in allowed:
                assert r.status_code < 300, (who, method, path, r.status_code, r.text)
            else:
                assert r.status_code in (403, 404), (who, method, path, r.status_code)


def test_group_admin_sees_only_own_group(world):
    r = world["c"]["admin_a"].get(f"{API}/groups")
    assert [g["name"] for g in r.json()] == ["Campus A"]
    emails = {u["email"] for u in world["c"]["admin_a"].get(f"{API}/groups/{world['a'].id}/users").json()}
    assert emails == {"admin@a.test", "user@a.test"}
    # User B is invisible through group A's routes
    ub = world["users"]["user@b.test"]["id"]
    r = world["c"]["admin_a"].patch(f"{API}/groups/{world['a'].id}/users/{ub}", json={"status": "disabled"})
    assert r.status_code == 404


def test_group_audit_is_scoped(world):
    events = world["c"]["admin_a"].get(f"{API}/groups/{world['a'].id}/audit").json()
    assert events and all(e["group_id"] == str(world["a"].id) for e in events)
    assert not any(e["actor_email"] and e["actor_email"].endswith("b.test") for e in events)


def test_domains_are_unique_across_groups(world):
    s = world["c"]["super"]
    r = s.post(f"{API}/admin/groups/{world['b'].id}/domains", json={"domain": "A.TEST"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "domain_taken"
    r = s.post(f"{API}/admin/groups/{world['b'].id}/domains", json={"domain": "not a domain"})
    assert r.status_code == 422


def test_removed_domain_stops_new_accounts_only(world, make_client):
    s = world["c"]["super"]
    assert s.delete(f"{API}/admin/groups/{world['a'].id}/domains/a.test").status_code == 200
    assert make_client().post(f"{API}/auth/code", json={"email": "new@a.test"}).status_code == 403
    assert make_client().post(f"{API}/auth/code", json={"email": "user@a.test"}).status_code == 202


def test_add_group_admin_preprovisions_and_checks_domain(world):
    s = world["c"]["super"]
    a = world["a"].id
    r = s.post(f"{API}/admin/groups/{a}/admins", json={"email": "dean@a.test"})
    assert r.status_code == 201
    assert {"role": "group_admin", "group_id": str(a)} in r.json()["roles"]
    r = s.post(f"{API}/admin/groups/{a}/admins", json={"email": "dean@elsewhere.test"})
    assert r.status_code == 422
    r = s.post(f"{API}/admin/groups/{a}/admins", json={"email": "user@b.test"})
    assert r.status_code == 409


def test_new_group_admin_gets_access_on_next_request(world):
    s, ua = world["c"]["super"], world["c"]["user_a"]
    a = world["a"].id
    assert ua.get(f"{API}/groups/{a}/users").status_code == 404
    s.post(f"{API}/admin/groups/{a}/admins", json={"email": "user@a.test"})
    assert ua.get(f"{API}/groups/{a}/users").status_code == 200
    s.delete(f"{API}/admin/groups/{a}/admins/{world['users']['user@a.test']['id']}")
    assert ua.get(f"{API}/groups/{a}/users").status_code == 404


def test_disabling_group_signs_out_members(world):
    s = world["c"]["super"]
    assert s.patch(f"{API}/admin/groups/{world['a'].id}", json={"status": "disabled"}).status_code == 200
    assert world["c"]["user_a"].get(f"{API}/me").status_code == 401
    assert world["c"]["admin_a"].get(f"{API}/me").status_code == 401
    assert world["c"]["user_b"].get(f"{API}/me").status_code == 200


def test_group_admin_cannot_disable_self_or_super_admin(world, db):
    g = world["a"]
    sa = make_user(db, "super2@a.test", g, role="super_admin")
    admin = world["c"]["admin_a"]
    me = admin.get(f"{API}/me").json()["id"]
    assert admin.patch(f"{API}/groups/{g.id}/users/{me}", json={"status": "disabled"}).status_code == 422
    assert admin.patch(f"{API}/groups/{g.id}/users/{sa.id}", json={"status": "disabled"}).status_code == 403


def test_cap_change_is_audited(world):
    admin = world["c"]["admin_a"]
    ua = world["users"]["user@a.test"]["id"]
    r = admin.patch(f"{API}/groups/{world['a'].id}/users/{ua}", json={"cap_pages": 250})
    assert r.json()["cap_pages"] == 250 and r.json()["effective_cap"] == 250
    events = admin.get(f"{API}/groups/{world['a'].id}/audit", params={"action": "quota.cap_set"}).json()
    assert events[0]["details"]["cap_pages"] == [None, 250]
    r = admin.patch(f"{API}/groups/{world['a'].id}/users/{ua}", json={"cap_pages": None})
    assert r.json()["cap_pages"] is None


def test_move_user_drops_admin_rights(world):
    s = world["c"]["super"]
    admin_a = world["users"]["admin@a.test"]["id"]
    r = s.patch(f"{API}/admin/users/{admin_a}", json={"group_id": str(world["b"].id)})
    assert r.status_code == 200
    assert r.json()["group_name"] == "Campus B" and r.json()["roles"] == []
    assert world["c"]["admin_a"].get(f"{API}/groups/{world['a'].id}").status_code == 404


def test_last_super_admin_cannot_be_removed(world):
    s = world["c"]["super"]
    root = world["users"]["root@admin.test"]["id"]
    r = s.delete(f"{API}/admin/users/{root}/super-admin")
    assert r.status_code in (409, 422)
    # With a second super admin in a group, the first can be removed once they have a group
    ua = world["users"]["user@a.test"]["id"]
    s.post(f"{API}/admin/users/{ua}/super-admin")
    s.patch(f"{API}/admin/users/{root}", json={"group_id": str(world["a"].id)})
    assert s.delete(f"{API}/admin/users/{root}/super-admin").status_code == 200
    r = world["c"]["user_a"].delete(f"{API}/admin/users/{ua}/super-admin")
    assert r.status_code == 409


def test_settings_validation_and_audit(world):
    s = world["c"]["super"]
    assert s.patch(f"{API}/admin/settings", json={"file_retention_days": 0}).status_code == 422
    assert s.patch(f"{API}/admin/settings", json={"max_file_mb": None}).status_code == 422
    assert s.patch(f"{API}/admin/settings", json={"bogus": 1}).status_code == 422
    r = s.patch(f"{API}/admin/settings", json={"file_retention_days": 14, "record_retention_days": 3650})
    assert r.json()["file_retention_days"] == 14
    ev = s.get(f"{API}/admin/audit", params={"action": "settings.updated"}).json()[0]
    assert ev["details"]["file_retention_days"] == [30, 14]


def test_user_search_escapes_wildcards(world):
    s = world["c"]["super"]
    assert len(s.get(f"{API}/admin/users", params={"q": "%"}).json()) == 0
    assert {u["email"] for u in s.get(f"{API}/admin/users", params={"q": "a.test"}).json()} == {
        "admin@a.test", "user@a.test"}
