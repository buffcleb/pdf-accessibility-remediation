"""#350: row-level security, the database's own check on group boundaries behind the API's.

The app connects as portal_app, which the policies bind (conftest.py). A probe route runs SQL straight through the
request's session, with no group check of the API's own, to show what the database alone lets through."""
from __future__ import annotations

import uuid

import pytest
from fastapi import Body, Depends
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portal import rls
from portal.db import get_db, get_engine, open_session, system_session
from portal.deps import Principal, current_principal
from portal.models import AuditEvent, HelpMessage, HelpRequest, Job, User
from tests.portal.conftest import login
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, setup, topup, upload  # noqa: F401

PROBE = f"{API}/test/rls"


@pytest.fixture
def probe(app):
    """POST {name: sql}: each query's first value, or its error, run as the caller with no check of the API's."""
    def run(queries: dict[str, str] = Body(...), p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
        out = {}
        for name, sql in queries.items():
            try:
                with db.begin_nested():
                    out[name] = db.execute(text(sql)).scalar()
            except DBAPIError as e:
                out[name] = "refused: " + str(e.orig).splitlines()[0]
        return out
    app.add_api_route(PROBE, run, methods=["POST"])


@pytest.fixture
def two_groups(setup, probe):
    """Documents, files, ledger entries, a help request and audit entries in both groups."""
    s = setup
    db = s["db"]
    for g in (s["a"], s["b"]):
        assert topup(s, g.id, 100).status_code == 201
    assert upload(s["c"]["user"], ("a.pdf", pdf_bytes(1))).status_code == 200
    assert upload(s["c"]["admin_b"], ("b.pdf", pdf_bytes(1))).status_code == 200
    for g in (s["a"], s["b"]):
        job = db.scalar(select(Job).where(Job.group_id == g.id))
        req = HelpRequest(job_id=job.id, filename=job.filename, user_id=job.user_id, group_id=g.id, kind="other")
        db.add(req)
        db.flush()
        db.add(HelpMessage(request_id=req.id, author_email="x@x.test", author_role="person", body="help"))
    db.commit()
    return s


def _counts(group_id: uuid.UUID) -> dict[str, str]:
    g = f"'{group_id}'"
    jobs = f"(select id from jobs where group_id = {g})"
    return {
        "jobs": f"select count(*) from jobs where group_id = {g}",
        "job_files": f"select count(*) from job_files where job_id in {jobs}",
        "ledger": f"select count(*) from quota_ledger where group_id = {g}",
        "help": f"select count(*) from help_requests where group_id = {g}",
        "help_messages": "select count(*) from help_messages m join help_requests r on r.id = m.request_id"
                         f" where r.group_id = {g}",
        "users": f"select count(*) from users where group_id = {g}",
        "audit": f"select count(*) from audit_events where group_id = {g}",
    }


def _owner_counts(db: Session, group_id: uuid.UUID) -> dict[str, int]:
    return {k: db.execute(text(sql)).scalar() for k, sql in _counts(group_id).items()}


def test_a_person_sees_only_their_groups_rows_even_without_the_apis_checks(two_groups):
    s = two_groups
    a, b = _owner_counts(s["db"], s["a"].id), _owner_counts(s["db"], s["b"].id)
    assert all(a.values()) and all(b.values())  # the owner (the tests' own session) sees both
    user = s["c"]["user"]
    assert user.post(PROBE, json=_counts(s["a"].id)).json() == a
    assert user.post(PROBE, json=_counts(s["b"].id)).json() == {k: 0 for k in b}
    # The same for a group's admin
    assert s["c"]["admin_b"].post(PROBE, json=_counts(s["a"].id)).json() == {k: 0 for k in a}
    assert s["c"]["admin_b"].post(PROBE, json=_counts(s["b"].id)).json() == b


def test_rows_cant_be_changed_or_moved_into_another_group(two_groups):
    s = two_groups
    a, b = s["a"].id, s["b"].id
    out = s["c"]["user"].post(PROBE, json={
        "change_b": f"with u as (update jobs set filename = 'x' where group_id = '{b}' returning 1)"
                    " select count(*) from u",
        "move_to_b": f"update jobs set group_id = '{b}' where group_id = '{a}' returning 1",
        "user_to_b": f"update users set group_id = '{b}' where email = 'user@a.test' returning 1",
    }).json()
    assert out["change_b"] == 0
    assert out["move_to_b"].startswith("refused: new row violates row-level security policy")
    assert out["user_to_b"].startswith("refused: new row violates row-level security policy")
    assert s["db"].scalar(select(Job.filename).where(Job.group_id == b)) == "b.pdf"


def test_an_admin_of_another_group_sees_that_group_too(two_groups, db):
    s = two_groups
    from portal.models import RoleAssignment
    admin_a = db.scalar(select(User).where(User.email == "admin@a.test"))
    db.add(RoleAssignment(user_id=admin_a.id, role="group_admin", group_id=s["b"].id))
    db.commit()
    out = s["c"]["admin_a"].post(PROBE, json={"a": _counts(s["a"].id)["jobs"], "b": _counts(s["b"].id)["jobs"]})
    assert out.json() == {"a": 1, "b": 1}


def test_people_see_the_super_admins_but_not_other_groups_people(two_groups):
    s = two_groups
    out = s["c"]["user"].post(PROBE, json={
        "super": "select count(*) from users where email = 'root@admin.test'",
        "other": "select count(*) from users where email = 'admin@b.test'",
    }).json()
    assert out == {"super": 1, "other": 0}


def test_super_admins_bypass_and_it_is_audited_once_per_sign_in_and_kind_of_request(two_groups, db, make_client):
    s = two_groups
    sup = s["c"]["super"]
    both = {"a": _counts(s["a"].id)["jobs"], "b": _counts(s["b"].id)["jobs"]}
    assert sup.post(PROBE, json=both).json() == {"a": 1, "b": 1}
    assert sup.post(PROBE, json=both).json() == {"a": 1, "b": 1}
    sup.get(f"{API}/me")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "rls.bypass",
                                               AuditEvent.actor_email == "root@admin.test")).all()
    routes = sorted(r.details["route"] for r in rows)
    assert routes.count(f"POST {PROBE}") == 1 and f"GET {API}/me" in routes
    # A new sign-in is audited afresh; people who aren't super admins never bypass
    again = make_client()
    login(again, "root@admin.test")
    again.post(PROBE, json=both)
    assert db.scalar(select(text("count(*)")).select_from(AuditEvent).where(
        AuditEvent.action == "rls.bypass", AuditEvent.details["route"].astext == f"POST {PROBE}")) == 2
    assert not db.scalars(select(AuditEvent).where(AuditEvent.action == "rls.bypass",
                                                   AuditEvent.actor_email != "root@admin.test",
                                                   AuditEvent.actor_email != "system")).all()


def test_the_worker_bypasses_and_it_is_audited_once(two_groups, db):
    for _ in range(2):
        with system_session("worker") as w:
            assert w.execute(text("select current_user")).scalar() == "portal_app"
            assert w.scalar(select(text("count(*)")).select_from(Job)) == 2
            w.commit()  # a later transaction in the same session keeps the bypass
            assert w.scalar(select(text("count(*)")).select_from(Job)) == 2
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "rls.bypass",
                                               AuditEvent.actor_email == "system")).all()
    assert [r.details for r in rows] == [{"reason": "worker"}]


def test_nothing_carries_over_on_a_pooled_connection(two_groups):
    s = two_groups
    s["c"]["super"].post(PROBE, json={"x": "select 1"})
    with system_session("worker") as w:
        w.execute(text("select 1"))
        w.commit()
    # A session nobody set up, and a bare connection, see no group's rows
    with open_session() as anon:
        assert anon.scalar(select(text("count(*)")).select_from(Job)) == 0
        assert anon.scalar(select(text("count(*)")).select_from(User)) == 0
    for _ in range(3):  # the pool's connections, whichever comes back
        with get_engine().connect() as c:
            assert c.execute(text("select coalesce(current_setting('portal.bypass', true), '')")).scalar() == ""
            assert c.execute(text("select count(*) from jobs")).scalar() == 0
    assert s["c"]["user"].post(PROBE, json={"b": _counts(s["b"].id)["jobs"]}).json() == {"b": 0}


def test_signing_in_sees_only_the_address_being_signed_in(two_groups, make_client):
    with open_session() as db:
        rls.identify(db, email="user@a.test")
        assert db.scalars(select(User.email)).all() == ["user@a.test"]
        rls.identify(db, email="nobody@a.test")
        assert db.scalars(select(User.email)).all() == []
    # And signing in works: an existing person, a new one by their domain, a super admin
    for email in ("user2@a.test", "new@b.test", "root@admin.test"):
        c = make_client()
        assert login(c, email)["user"]["email"] == email
        assert c.get(f"{API}/me").status_code == 200
