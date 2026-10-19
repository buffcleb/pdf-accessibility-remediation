"""#326: what people type into searches, sorts and filters is only ever a value. Every value goes to SQLAlchemy as a
bound parameter, searches escape LIKE's wildcards, and sort, column and filter names are looked up in the report's
own lists; this sends the usual attack strings everywhere they can go and checks the answers stay normal (no
server error) and nothing in the database changes. CI's "SQL built from strings" job (Bandit B608) keeps new code
from formatting a query out of text."""
from __future__ import annotations

import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import func, select

from portal import live_reports
from portal.models import AuditEvent, Group, Job, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from tests.portal.test_live_reports import REPORTS, SUPER_ONLY

ATTACKS = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "'; DROP TABLE jobs; --",
    "\"; DELETE FROM users; --",
    "1; UPDATE users SET is_super_admin = true",
    "') UNION SELECT email, null FROM users --",
    "%",
    "_",
    "\\",
    "'\\''",
    "-created_at; DROP TABLE jobs",
    "\x00",
    "x" * 190,
]


def _counts(db):
    db.expire_all()
    return {m.__name__: db.scalar(select(func.count()).select_from(m)) for m in (Job, User, Group)}


def _ok(r):
    # A refusal (400/404/422) is fine; a server error isn't, nor is anything but an ordinary answer
    assert r.status_code < 500, (r.request.url, r.status_code, r.text[:300])


@pytest.fixture
def data(setup):
    s = setup
    topup(s, s["a"].id, 100, ticket_ref="RITM0000001")
    job_id = upload(s["c"]["user"], ("annual-report.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    return s


def test_live_reports_take_attack_strings_as_values(data):
    s = data
    before = _counts(s["db"])
    for name in REPORTS + SUPER_ONLY:
        body = s["c"]["super"].get(f"{API}/reports/{name}").json()
        filters = [f["key"] for f in body.get("filters") or []]
        for attack in ATTACKS:
            for params in ({"q": attack}, {"sort": attack}, {"sort": f"-{attack}"}, {"cols": attack},
                           {attack[:40] or "x": attack}, *({k: attack} for k in filters)):
                r = s["c"]["super"].get(f"{API}/reports/{name}", params=params)
                _ok(r)
                searchable = bool(live_reports.REPORTS[name].search)  # one without search ignores it
                if r.status_code == 200 and "q" in params and params["q"] not in ("%", "_") and searchable:
                    rows = r.json()["rows"]
                    if name == "audit":  # the CSV exports below are audited with their search: real matches
                        assert all("report.exported" in str(row) for row in rows), (name, params, rows[:2])
                    else:
                        assert r.json()["total_rows"] == 0, (name, params)
            _ok(s["c"]["super"].get(f"{API}/reports/{name}.csv", params={"q": attack, "sort": attack}))
            _ok(s["c"]["admin_a"].get(f"{API}/reports/{name}", params={"q": attack, "sort": attack}))
    assert _counts(s["db"]) == before
    # ...and they still work
    rows = s["c"]["super"].get(f"{API}/reports/documents", params={"q": "annual"}).json()["rows"]
    assert [r["filename"] for r in rows] == ["annual-report.pdf"]


def test_a_like_wildcard_is_a_character_not_a_wildcard(data):
    s = data
    for q in ("%", "_", "annual%", "a_nual"):
        assert s["c"]["super"].get(f"{API}/reports/documents", params={"q": q}).json()["total_rows"] == 0, q
        assert s["c"]["user"].get(f"{API}/documents", params={"q": q}).json()["total"] == 0, q


def test_every_other_search_and_filter_takes_attack_strings_as_values(data):
    s = data
    a = s["a"].id
    before = _counts(s["db"])
    for attack in ATTACKS:
        for who, path, params in [
            ("user", "/documents", {"q": attack}),
            ("user", "/documents", {"sort": attack[:20]}),
            ("user", "/documents", {"status": attack[:20], "result": attack[:20], "service": attack[:64]}),
            ("admin_a", f"/groups/{a}/documents", {"q": attack, "sort": attack[:20], "status": attack[:20]}),
            ("admin_a", f"/groups/{a}/users", {"q": attack}),
            ("admin_a", f"/groups/{a}/audit", {"action": attack[:64]}),
            ("admin_a", f"/groups/{a}/jobs", {"status": attack[:16]}),
            ("admin_a", f"/groups/{a}/ledger", {"kind": attack[:16]}),
            ("super", "/admin/users", {"q": attack}),
            ("super", "/admin/audit", {"action": attack[:64]}),
            ("super", "/admin/help", {"q": attack, "status": attack[:16], "kind": attack[:16]}),
            ("super", "/admin/queues/live", {"q": attack, "queue": attack[:80], "state": attack[:16]}),
            ("super", "/admin/access-check", {"email": attack, "service": attack[:64]}),
        ]:
            r = s["c"][who].get(f"{API}{path}", params=params)
            _ok(r)
            if r.status_code == 200 and "q" in params and path.endswith(("documents", "users")):
                body = r.json()
                rows = body if isinstance(body, list) else body.get("items", body.get("rows", []))
                assert not rows, (path, params)
        # An ID that isn't one is refused before any query runs
        bad = quote(attack, safe="")  # as a browser sends it
        for path in (f"/jobs/{bad}", f"/groups/{bad}/users", f"/admin/users/{bad}/super-admin"):
            _ok(s["c"]["super"].get(f"{API}{path}"))
    assert _counts(s["db"]) == before
    for who in ("user", "admin_a"):  # nobody became a super admin
        assert s["c"][who].get(f"{API}/me").json()["is_super_admin"] is False, who
    assert s["db"].scalar(select(func.count()).select_from(AuditEvent)) > 0


def test_a_nul_character_is_refused_plainly_not_a_server_error(data):
    """Postgres can't keep or compare a NUL in text: a search with one was a 500 until #326."""
    s = data
    for who, path in (("user", "/documents"), ("super", "/reports/documents"), ("super", "/admin/users")):
        r = s["c"][who].get(f"{API}{path}", params={"q": "annual\x00"})
        assert r.status_code == 400 and "NUL" in r.json()["detail"]["message"], (path, r.status_code, r.text[:200])
