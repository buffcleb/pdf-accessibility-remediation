"""My documents: a page at a time, with search, filters and sorting, only ever the person's own."""
from __future__ import annotations

import uuid

from portal.models import Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

D = f"{API}/documents"


def _docs(s):
    topup(s, s["a"].id, 200)
    ids = {}
    for name, pages in [("Annual report.pdf", 3), ("budget_2026.pdf", 1), ("Course catalog.pdf", 5), ("minutes.pdf", 2)]:
        ids[name] = upload(s["c"]["user"], (name, pdf_bytes(pages))).json()[0]["job"]["id"]
    upload(s["c"]["user2"], ("someone else's.pdf", pdf_bytes(1)))
    db = s["db"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(ids["Annual report.pdf"])).engine_job_id)
    s["engine"].fail(db.get(Job, uuid.UUID(ids["minutes.pdf"])).engine_job_id)
    run_worker(db)
    return ids


def _names(body):
    return [d["filename"] for d in body["items"]]


def test_paging_and_sorting(setup):
    s = setup
    _docs(s)
    c = s["c"]["user"]
    body = c.get(D, params={"page_size": 2}).json()
    assert body["total"] == 4 and body["page_size"] == 2 and len(body["items"]) == 2  # never someone else's
    assert _names(c.get(D, params={"page_size": 2, "page": 2}).json()) and body["active"] == 2
    assert _names(c.get(D, params={"sort": "filename"}).json()) == [
        "Annual report.pdf", "budget_2026.pdf", "Course catalog.pdf", "minutes.pdf"]  # case doesn't matter
    assert _names(c.get(D, params={"sort": "-pages"}).json())[0] == "Course catalog.pdf"
    assert c.get(D, params={"sort": "owner"}).status_code == 422


def test_search_and_filters(setup):
    s = setup
    _docs(s)
    c = s["c"]["user"]
    assert _names(c.get(D, params={"q": "REPORT"}).json()) == ["Annual report.pdf"]
    assert _names(c.get(D, params={"q": "_2026"}).json()) == ["budget_2026.pdf"]  # "_" is a letter, not a wildcard
    assert _names(c.get(D, params={"q": "%"}).json()) == []
    assert _names(c.get(D, params={"status": "done"}).json()) == ["Annual report.pdf"]
    assert _names(c.get(D, params={"status": "failed"}).json()) == ["minutes.pdf"]
    assert sorted(_names(c.get(D, params={"status": "in_progress"}).json())) == ["Course catalog.pdf", "budget_2026.pdf"]
    assert _names(c.get(D, params={"result": "passes"}).json()) == ["Annual report.pdf"]
    assert _names(c.get(D, params={"result": "review"}).json()) == ["Annual report.pdf"]  # it has findings
    assert c.get(D, params={"result": "needs_work"}).json()["total"] == 0
    assert c.get(D, params={"service": "standard", "audit": "false"}).json()["total"] == 4
    assert c.get(D, params={"audit": "true"}).json()["total"] == 0
    assert c.get(D, params={"since": "2999-01-01T00:00:00Z"}).json()["total"] == 0
    assert c.get(D, params={"status": "lost"}).status_code == 422
