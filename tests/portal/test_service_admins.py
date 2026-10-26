"""#564: service admins, between super admins and group admins. They help run every group (groups, people, pages,
files), without the service's own settings, its services and AI levels, or who is an admin above group admin."""
from __future__ import annotations

import uuid

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from portal import deps
from portal.main import create_app
from portal.models import AuditEvent, Job, RoleAssignment, User
from tests.portal.conftest import login, make_group, make_user
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, upload  # noqa: F401


@pytest.fixture
def world(db, make_client, fake_engine):
    """Two groups, each with an admin and a user; a super admin and a service admin, neither in a group."""
    a = make_group(db, "Campus A", "a.test")
    b = make_group(db, "Campus B", "b.test")
    make_user(db, "root@admin.test", None, role="super_admin")
    make_user(db, "svc@admin.test", None, role="service_admin")
    make_user(db, "svc2@admin.test", None, role="service_admin")
    make_user(db, "admin@a.test", a, admin_of=a)
    make_user(db, "admin@b.test", b, admin_of=b)
    c = {}
    for name, email in [("super", "root@admin.test"), ("service", "svc@admin.test"), ("admin_a", "admin@a.test"),
                        ("user_a", "user@a.test"), ("user_b", "user@b.test")]:
        c[name] = make_client()
        login(c[name], email)  # a service admin with no group signs in, as a super admin does
    users = {u.email: u for u in db.scalars(select(User))}
    return {"a": a, "b": b, "c": c, "db": db, "users": users, "engine": fake_engine}


def _id(w, email):
    return str(w["users"][email].id)


# --- what they may do --------------------------------------------------------------------------------

def _allowed(w):
    """Requests a service admin may make (each should succeed), in order."""
    a, b = w["a"].id, w["b"].id
    ua, ub = _id(w, "user@a.test"), _id(w, "user@b.test")
    return [
        ("get", "/admin/groups", None),
        ("get", f"/admin/groups/{b}", None),
        ("post", "/admin/groups", {"name": "Campus C", "domains": ["c.test"]}),
        ("patch", f"/admin/groups/{b}", {"name": "Campus Bee", "default_user_cap": 5}),
        ("post", f"/admin/groups/{b}/domains", {"domain": "b2.test"}),
        ("delete", f"/admin/groups/{b}/domains/b2.test", None),
        ("post", f"/admin/groups/{b}/emails", {"email": "guest@elsewhere.test"}),
        ("delete", f"/admin/groups/{b}/emails/guest@elsewhere.test", None),
        ("get", f"/admin/groups/{b}/admins", None),
        ("post", f"/admin/groups/{b}/admins", {"email": "user@b.test"}),
        ("delete", f"/admin/groups/{b}/admins/{ub}", None),
        ("get", "/admin/users", None),
        ("get", "/admin/people?role=service_admin", None),
        ("patch", f"/admin/users/{ua}", {"name": "User A"}),
        ("post", f"/admin/users/{ub}/sessions/end", None),
        ("post", f"/admin/groups/{a}/topups", {"pages": 100, "ticket_ref": "RITM0012345"}),
        ("post", f"/admin/groups/{a}/topups", {"pages": 10, "override": True, "comment": "Agreed by phone"}),
        ("post", f"/admin/groups/{a}/adjustments", {"pages": -5, "comment": "Counted twice", "ticket_ref": "RITM1"}),
        ("post", f"/admin/users/{ua}/usage-corrections", {"pages": 1, "comment": "Missed a page"}),
        # Every group, as its own admins see it
        ("get", "/groups", None),
        ("get", f"/groups/{b}/people", None),
        ("patch", f"/groups/{b}/users/{ub}", {"cap_pages": 20}),
        ("patch", f"/groups/{b}", {"require_approval": True}),
        ("get", f"/groups/{b}/documents", None),
        ("get", f"/groups/{b}/ledger", None),
        ("get", f"/groups/{b}/api-tokens", None),
        ("get", f"/groups/{b}/attention", None),
    ]


def test_service_admins_help_run_every_group(world):
    svc = world["c"]["service"]
    for method, path, body in _allowed(world):
        r = getattr(svc, method)(API + path, **({"json": body} if body is not None else {}))
        assert r.status_code < 300, (method, path, r.status_code, r.text)
    assert {g["name"] for g in svc.get(f"{API}/groups").json()} == {"Campus A", "Campus Bee", "Campus C"}
    # What they did is theirs in the audit log, and the group's log says it came from outside the group
    db = world["db"]
    corrected = db.scalar(select(AuditEvent).where(AuditEvent.action == "quota.usage_corrected"))
    assert corrected.actor_email == "svc@admin.test" and corrected.details["by_service_admin"] \
        and not corrected.details["by_super_admin"]
    cap = db.scalar(select(AuditEvent).where(AuditEvent.action == "quota.cap_set"))
    assert cap.details["by_service_admin"]
    override = db.scalar(select(AuditEvent).where(AuditEvent.action == "quota.topup", AuditEvent.outcome == "override"))
    assert override.actor_email == "svc@admin.test" and override.details["comment"] == "Agreed by phone"


def test_service_admins_move_people_between_groups(world):
    svc, ua = world["c"]["service"], _id(world, "user@a.test")
    r = svc.patch(f"{API}/admin/users/{ua}", json={"group_id": str(world["b"].id)})
    assert r.status_code == 200 and r.json()["group_id"] == str(world["b"].id)
    # ...but a member can't be left without a group
    r = svc.patch(f"{API}/admin/users/{ua}", json={"group_id": None})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "group_required"


def _refused(w):
    """Requests a service admin may not make: the service's own settings, services, roles, people's data."""
    b = w["b"].id
    root, svc2 = _id(w, "root@admin.test"), _id(w, "svc2@admin.test")
    ua = _id(w, "user@a.test")
    return [
        ("patch", f"/admin/groups/{b}", {"status": "disabled"}),
        ("get", "/admin/settings", None),
        ("patch", "/admin/settings", {"max_file_mb": 50}),
        ("patch", "/admin/services/standard", {"enabled": False}),
        ("post", "/admin/services/standard/topups", {"pages": 100, "ticket_ref": "RITM0012345"}),
        ("get", "/admin/ai-models", None),
        ("get", "/admin/links", None),
        ("put", "/admin/branding/logo", None),
        ("post", "/admin/mail/test", {"to": "x@a.test"}),
        ("post", f"/admin/users/{ua}/super-admin", None),
        ("post", f"/admin/users/{ua}/service-admin", None),
        ("delete", f"/admin/users/{svc2}/service-admin", None),
        ("delete", f"/admin/users/{root}/super-admin", None),
        ("get", f"/admin/users/{ua}/export", None),
        ("post", f"/admin/users/{ua}/remove", {"confirm_email": "user@a.test", "reason": "asked"}),
        # Super admins' and other service admins' accounts are super admins' to change
        ("patch", f"/admin/users/{root}", {"status": "disabled"}),
        ("patch", f"/admin/users/{svc2}", {"status": "disabled"}),
        ("post", f"/admin/users/{root}/sessions/end", None),
        ("post", "/admin/monitoring/grafana", None),
    ]


def test_service_admins_leave_the_service_itself_to_super_admins(world):
    svc = world["c"]["service"]
    for method, path, body in _refused(world):
        r = getattr(svc, method)(API + path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    assert world["db"].get(type(world["b"]), world["b"].id).status == "active"


def test_group_admins_cant_disable_a_service_admin_in_their_group(world, db):
    member = make_user(db, "helper@a.test", world["a"])
    db.add(RoleAssignment(user_id=member.id, role="service_admin"))
    db.commit()
    r = world["c"]["admin_a"].patch(f"{API}/groups/{world['a'].id}/users/{member.id}", json={"status": "disabled"})
    assert r.status_code == 403


def test_group_admins_and_members_get_none_of_it(world):
    for who in ("admin_a", "user_a"):
        for method, path, body in _allowed(world)[:19]:  # the routes under /admin
            r = getattr(world["c"][who], method)(API + path, **({"json": body} if body is not None else {}))
            assert r.status_code in (403, 404), (who, method, path, r.status_code)


def test_me_says_what_they_may_do(world):
    me = world["c"]["service"].get(f"{API}/me").json()
    assert me["is_service_admin"] and not me["is_super_admin"] and me["admin_of"] == [] and me["can_submit"]
    assert set(me["permissions"]) == {"groups.all", "groups.manage", "group_admins", "people", "pages", "files.staff",
                                      "costs", "help", "queues", "queues.items", "documents.rerun_remove", "attention",
                                      "system", "reports.all"}
    assert world["c"]["admin_a"].get(f"{API}/me").json()["permissions"] == []
    assert "roles" in world["c"]["super"].get(f"{API}/me").json()["permissions"]


def test_their_bypass_is_audited_as_their_own(world, db):
    world["c"]["service"].get(f"{API}/groups")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "rls.bypass",
                                               AuditEvent.actor_email == "svc@admin.test")).all()
    assert rows and all(r.details["role"] == "service_admin" for r in rows)
    assert f"GET {API}/groups" in {r.details["route"] for r in rows}


# --- documents and files -----------------------------------------------------------------------------

def test_service_admins_open_any_groups_documents_and_the_engines_reports(world, db):
    w = world
    w["c"]["super"].post(f"{API}/admin/groups/{w['b'].id}/topups", json={"pages": 100, "ticket_ref": "RITM0012345"})
    job_id = upload(w["c"]["user_b"], ("doc.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(db)
    w["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    job = db.get(Job, uuid.UUID(job_id))
    job.cost = {**(job.cost or {}), "ai_usd": 0.2767}
    db.commit()
    svc = w["c"]["service"]
    seen = svc.get(f"{API}/jobs/{job_id}").json()
    by_kind = {f["kind"]: f["id"] for f in seen["files"]}
    assert "report_json" in by_kind and seen["staff"]["engine_job_id"]  # the Run details
    assert seen["cost"]["ai_usd"] == 0.2767
    assert svc.get(f"{API}/jobs/{job_id}/files/{by_kind['report_json']}").status_code == 200
    assert svc.get(f"{API}/jobs/{job_id}/files/{by_kind['report_pdf']}").status_code == 200
    opened = db.scalars(select(AuditEvent).where(AuditEvent.action == "file.accessed",
                                                 AuditEvent.actor_email == "svc@admin.test")).all()
    assert {e.details["kind"] for e in opened} == {"report_json", "report_pdf"}
    assert all(e.details["owner"] == "user@b.test" and e.group_id == w["b"].id for e in opened)
    # Another group's admin still can't
    assert w["c"]["admin_a"].get(f"{API}/jobs/{job_id}").status_code == 404
    # A deleted document's record stays visible to them, as to super admins
    job.deleted_at = job.finished_at
    db.commit()
    assert svc.get(f"{API}/jobs/{job_id}").status_code == 200
    assert w["c"]["user_b"].get(f"{API}/jobs/{job_id}").status_code == 404


# --- the role itself -----------------------------------------------------------------------------------

def test_super_admins_make_and_remove_service_admins(world, db):
    w = world
    sup, ua = w["c"]["super"], _id(w, "user@a.test")
    r = sup.post(f"{API}/admin/users/{ua}/service-admin")
    assert r.status_code == 200 and {"role": "service_admin", "group_id": None} in r.json()["roles"]
    assert sup.post(f"{API}/admin/users/{ua}/service-admin").status_code == 200  # again: no change
    granted = db.scalars(select(AuditEvent).where(AuditEvent.action == "role.granted",
                                                  AuditEvent.details["role"].astext == "service_admin")).all()
    assert len(granted) == 1 and granted[0].details["email"] == "user@a.test"
    # They now see every group
    assert {g["name"] for g in w["c"]["user_a"].get(f"{API}/groups").json()} == {"Campus A", "Campus B"}
    r = sup.delete(f"{API}/admin/users/{ua}/service-admin")
    assert r.status_code == 200 and all(x["role"] != "service_admin" for x in r.json()["roles"])
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "role.revoked",
                                              AuditEvent.details["role"].astext == "service_admin"))
    assert w["c"]["user_a"].get(f"{API}/groups").json() == []
    assert sup.delete(f"{API}/admin/users/{ua}/service-admin").status_code == 404


def test_a_service_admin_without_a_group_keeps_the_role_until_given_one(world):
    sup, svc = world["c"]["super"], _id(world, "svc@admin.test")
    r = sup.delete(f"{API}/admin/users/{svc}/service-admin")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "group_required"


def test_a_super_admin_isnt_made_a_service_admin_and_a_new_super_admin_drops_it(world, db):
    sup = world["c"]["super"]
    r = sup.post(f"{API}/admin/users/{_id(world, 'root@admin.test')}/service-admin")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "is_super_admin"
    svc = _id(world, "svc@admin.test")
    r = sup.post(f"{API}/admin/users/{svc}/super-admin")
    assert r.status_code == 200 and [x["role"] for x in r.json()["roles"]] == ["super_admin"]


# --- every route decides who may use it ---------------------------------------------------------------

def _routes(routes, prefix=""):
    for r in routes:
        if hasattr(r, "include_context"):  # an included router (FastAPI 0.14x keeps it whole)
            yield from _routes(r.original_router.routes, prefix + r.include_context.prefix)
        elif isinstance(r, APIRoute):
            yield prefix + r.path, r


def _guard(dependant) -> str:
    """The strictest check a route's dependencies make before its own code runs."""
    found = set()

    def walk(d):
        for sub in d.dependencies:
            call = sub.call
            if call is deps.require_super_admin:
                found.add("super")
            elif call is deps.require_any_admin:
                found.add("any_admin")
            elif getattr(call, "permission", None):
                found.add(call.permission)
            elif call is deps.current_principal or getattr(call, "__qualname__", "").startswith("document_principal"):
                found.add("signed_in")
            walk(sub)
    walk(dependant)
    for strict in ("super", *sorted(found - {"super", "any_admin", "signed_in"}), "any_admin", "signed_in"):
        if strict in found:
            return strict
    return "own_check"


# "signed_in": anyone signed in; the route's own code scopes it (their documents, the groups they administer, a
# super admin's or service admin's reach). "own_check": not signed in at all, or checked its own way (a signed link,
# the SIEM's key, the health checks).
GUARDS = {
    "super": """
        GET /admin/settings  PATCH /admin/settings
        POST /admin/services/{key}/kinds  PATCH /admin/services/{key}/kinds/{kind}  DELETE /admin/services/{key}/kinds/{kind}
        GET /admin/links  GET /admin/ai-models  POST /admin/services  PATCH /admin/services/{key}
        DELETE /admin/services/{key}  POST /admin/services/{key}/topups  POST /admin/services/{key}/adjustments
        PUT /admin/branding/logo  DELETE /admin/branding/logo  GET /admin/mail  POST /admin/mail/test
    """,
    "groups.all": "GET /admin/groups  GET /admin/groups/{group_id}  GET /admin/services",
    "help": """
        POST /help/{request_id}/status  POST /help/{request_id}/assign  GET /admin/help/list  GET /admin/help
        POST /help/{request_id}/corrected  POST /help/{request_id}/resolve  POST /help/{request_id}/save
    """,
    "queues": "GET /admin/queues  GET /admin/queues/live  GET /admin/queues/{key}/items",
    "queues.pause": "POST /admin/queues/{key}/pause  POST /admin/queues/{key}/resume",
    "queues.items": """
        POST /admin/queues/{key}/items/{item_id}/retry  POST /admin/queues/{key}/items/{item_id}/cancel
        POST /admin/queues/{key}/items/{item_id}/stop  POST /admin/queues/{key}/items/{item_id}/restart
        POST /admin/queues/{key}/items/{item_id}/remove  POST /admin/queues/{key}/items/{item_id}/process-now
    """,
    "documents.rerun_remove": "POST /jobs/{job_id}/rerun  POST /admin/jobs/remove",
    "attention": """
        GET /admin/attention  GET /admin/attention/acknowledged  POST /admin/attention/acknowledge
        DELETE /admin/attention/acknowledge  GET /admin/access-check
    """,
    "system": "GET /admin/monitoring/system  GET /admin/monitoring/workloads  GET /admin/audit/siem",
    "grafana": "POST /admin/monitoring/grafana",
    "reports.all": "GET /admin/reports/{name}.csv  GET /admin/audit",
    "groups.manage": """
        POST /admin/groups  PATCH /admin/groups/{group_id}
        POST /admin/groups/{group_id}/domains  DELETE /admin/groups/{group_id}/domains/{domain}
        POST /admin/groups/{group_id}/emails  DELETE /admin/groups/{group_id}/emails/{email}
    """,
    "group_admins": """
        GET /admin/groups/{group_id}/admins  POST /admin/groups/{group_id}/admins
        DELETE /admin/groups/{group_id}/admins/{user_id}
    """,
    "people": "GET /admin/users  GET /admin/people  PATCH /admin/users/{user_id}  POST /admin/users/{user_id}/sessions/end",
    "people.data": "POST /admin/users/{user_id}/remove  GET /admin/users/{user_id}/export",
    "roles": """
        POST /admin/users/{user_id}/super-admin  DELETE /admin/users/{user_id}/super-admin
        POST /admin/users/{user_id}/service-admin  DELETE /admin/users/{user_id}/service-admin
    """,
    "pages": """
        POST /admin/groups/{group_id}/topups  POST /admin/groups/{group_id}/adjustments
        POST /admin/users/{user_id}/usage-corrections
    """,
    "any_admin": "GET /reports  GET /reports/{name}.csv  GET /reports/{name}/neighbours/{job_id}  GET /reports/{name}",
    "signed_in": """
        POST /auth/logout-all  GET /me  GET /services  GET /upload-options  POST /jobs  GET /jobs  GET /documents
        GET /documents/neighbours/{job_id}  GET /jobs/{job_id}  POST /jobs/{job_id}/confirm  POST /jobs/confirm
        POST /jobs/{job_id}/cancel  POST /jobs/{job_id}/remove-early  POST /jobs/remove-early
        POST /jobs/{job_id}/keep  GET /jobs/{job_id}/files/{file_id}  GET /jobs/{job_id}/report
        POST /documents/export/plan  POST /documents/export  GET /jobs/{job_id}/files.zip
        GET /jobs/{job_id}/versions  GET /jobs/{job_id}/versions/{number}/pdf
        GET /jobs/{job_id}/versions/{number}/tagmap  POST /jobs/{job_id}/versions
        POST /jobs/{job_id}/versions/{number}/undo-save  GET /jobs/{job_id}/tag-draft  PUT /jobs/{job_id}/tag-draft
        DELETE /jobs/{job_id}/tag-draft  GET /jobs/{job_id}/tag-draft/{user_id}
        POST /jobs/{job_id}/versions/{number}/describe  POST /jobs/{job_id}/versions/{number}/review
        GET /jobs/{job_id}/finding-marks  PUT /jobs/{job_id}/finding-marks/{key}  GET /jobs/{job_id}/findings.csv
        GET /groups  GET /groups/browse  GET /groups/{group_id}  GET /groups/{group_id}/users
        GET /groups/{group_id}/people  PATCH /groups/{group_id}/users/{user_id}  PATCH /groups/{group_id}
        POST /groups/{group_id}/subgroups
        POST /groups/{group_id}/users/{user_id}/approve  POST /groups/{group_id}/users/{user_id}/decline
        PATCH /groups/{group_id}/services/{key}  PUT /groups/{group_id}/users/{user_id}/services/{key}
        GET /groups/{group_id}/audit  GET /groups/{group_id}/attention
        GET /groups/{group_id}/attention/acknowledged  POST /groups/{group_id}/attention/acknowledge
        DELETE /groups/{group_id}/attention/acknowledge  GET /groups/{group_id}/access-check
        GET /groups/{group_id}/jobs  GET /groups/{group_id}/documents
        GET /groups/{group_id}/documents/neighbours/{job_id}  POST /groups/{group_id}/documents/export/plan
        POST /groups/{group_id}/documents/export  GET /groups/{group_id}/ledger
        GET /groups/{group_id}/reports/{name}.csv  POST /jobs/{job_id}/help  GET /jobs/{job_id}/help
        GET /help/mine  GET /help/{request_id}  POST /help/{request_id}/messages
        GET /help/{request_id}/attachments/{attachment_id}  GET /groups/{group_id}/help/list
        GET /groups/{group_id}/help  POST /help/{request_id}/reopen  GET /me/email-reports  PUT /me/email-reports
        GET /me/notifications  PUT /me/notifications  POST /me/seen  POST /me/email-reports/sample
        GET /me/report-views  GET /me/report-views/recipients  POST /me/report-views
        PATCH /me/report-views/{view_id}  DELETE /me/report-views/{view_id}  POST /me/report-views/{view_id}/leave
        POST /me/report-views/{view_id}/sample  GET /me/api-tokens  POST /me/api-tokens
        DELETE /me/api-tokens/{token_id}  GET /groups/{group_id}/api-tokens
        DELETE /groups/{group_id}/api-tokens/{token_id}
    """,
    "own_check": """
        POST /auth/code  POST /auth/code/verify  POST /auth/refresh  POST /auth/logout
        GET /branding  GET /branding/logo  GET /email-reports/unsubscribe  POST /email-reports/unsubscribe
        GET /monitoring/grafana-auth  GET /admin/audit/export  GET /live  GET /status  GET /health
    """,
}


def test_every_route_says_who_may_use_it(db):
    """A new route fails here until it's listed with its check: someone has decided who may use it."""
    want = {}
    for guard, text in GUARDS.items():
        words = text.split()
        for method, path in zip(words[::2], words[1::2]):
            assert f"{method} {path}" not in want, f"{method} {path} is listed twice"
            want[f"{method} {path}"] = guard
    got = {}
    for path, route in _routes(create_app().routes):
        if not path.startswith(API):
            continue
        for method in route.methods:
            got[f"{method} {path.removeprefix(API)}"] = _guard(route.dependant)
    wrong = {k: (v, want.get(k, "not listed")) for k, v in got.items() if v != want.get(k)}
    assert not wrong, f"route: (its check, what's listed here): {wrong}"
    assert not set(want) - set(got), f"listed but gone: {set(want) - set(got)}"
