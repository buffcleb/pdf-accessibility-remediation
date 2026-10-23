"""#514 part 2b: Help requests and Queue items, a page at a time on the server, with search, filters and sort."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal.models import HelpRequest, Job, User
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

HELP = f"{API}/admin/help/list"


def _requests(s, n=30):
    """`n` requests from user@a.test, created a minute apart (the first is the oldest), all open; then one of each
    other status, and a saved one."""
    db = s["db"]
    user = db.scalar(select(User).where(User.email == "user@a.test"))
    start = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(n):
        db.add(HelpRequest(filename=f"doc{i:02d}.pdf", user_id=user.id, group_id=s["a"].id, kind="alt_text", status="open",
                           created_at=start + timedelta(minutes=i), updated_at=start + timedelta(minutes=i)))
    for status in ("waiting", "resolved", "closed"):
        db.add(HelpRequest(filename=f"{status}.pdf", user_id=user.id, group_id=s["a"].id, kind="tables", status=status,
                           created_at=start, updated_at=start, status_changed_at=start))
    # In progress, the person wrote after us: it needs us again
    db.add(HelpRequest(filename="replied.pdf", user_id=user.id, group_id=s["a"].id, kind="other", status="in_progress",
                       created_at=start, updated_at=start, staff_wrote_at=start, person_wrote_at=start + timedelta(hours=2)))
    db.add(HelpRequest(filename="kept.pdf", user_id=user.id, group_id=s["a"].id, kind="other", status="closed",
                       saved=True, created_at=start, updated_at=start))
    db.commit()


def _names(body):
    return [r["filename"] for r in body["items"]]


def test_help_requests_a_page_at_a_time_oldest_wait_first(setup):
    s = setup
    _requests(s)
    sup = s["c"]["super"]
    first = sup.get(HELP).json()
    assert first["total"] == 31 and first["needs_us"] == 31 and len(first["items"]) == 25  # 30 open + the reply
    assert _names(first)[:2] == ["doc00.pdf", "doc01.pdf"]  # waited longest first
    rest = _names(sup.get(HELP, params={"page": 2}).json())
    assert len(set(_names(first) + rest)) == 31 and "replied.pdf" in rest  # it waits since the person wrote back
    assert _names(sup.get(HELP, params={"sort": "-waiting"}).json())[0] == "replied.pdf"
    assert sup.get(HELP, params={"sort": "mood"}).status_code == 422
    assert s["c"]["admin_a"].get(HELP).status_code == 403


def test_help_requests_views_filters_and_search(setup):
    s = setup
    _requests(s, 3)
    sup = s["c"]["super"]
    assert sup.get(HELP, params={"show": "active"}).json()["total"] == 5  # open, in progress, waiting
    assert _names(sup.get(HELP, params={"show": "resolved"}).json()) == ["resolved.pdf"]
    assert sup.get(HELP, params={"show": "all"}).json()["total"] == 7
    assert sup.get(HELP, params={"show": "everything"}).status_code == 422
    assert _names(sup.get(HELP, params={"saved": "true"}).json()) == ["kept.pdf"]
    assert sup.get(HELP, params={"show": "all", "kind": "tables"}).json()["total"] == 3
    assert sup.get(HELP, params={"show": "all", "group_id": str(s["b"].id)}).json()["total"] == 0
    assert _names(sup.get(HELP, params={"show": "all", "q": "DOC01"}).json()) == ["doc01.pdf"]
    assert sup.get(HELP, params={"show": "all", "q": "user@a"}).json()["total"] == 7  # by person
    assert sup.get(HELP, params={"show": "all", "q": "_"}).json()["total"] == 0
    # The "needs us" count is for the Show list: the same whatever the filters
    assert sup.get(HELP, params={"show": "all", "kind": "tables"}).json()["needs_us"] == 4


def test_a_groups_help_requests_for_its_admins(setup):
    s = setup
    _requests(s, 3)
    url = f"{API}/groups/{s['a'].id}/help/list"
    body = s["c"]["admin_a"].get(url).json()
    assert body["total"] == 8  # everything, saved too, newest activity first
    assert s["c"]["admin_a"].get(url, params={"show": "needs_us"}).json()["total"] == 4  # 3 open + the reply
    assert s["c"]["admin_a"].get(url, params={"q": "replied"}).json()["total"] == 1
    assert s["c"]["admin_b"].get(url).status_code in (403, 404)
    assert s["c"]["user"].get(url).status_code in (403, 404)


def test_queue_items_a_page_at_a_time_and_searched(setup, monkeypatch):
    s = setup
    topup(s, s["a"].id, 500)
    from portal import worker
    monkeypatch.setattr(worker, "submit_queued", lambda db: None)  # nothing is sent: they all wait
    for i in range(28):
        upload(s["c"]["user"], (f"wait{i:02d}.pdf", pdf_bytes(1)))
    upload(s["c"]["user2"], ("theirs.pdf", pdf_bytes(1)))
    sup = s["c"]["super"]
    queues = sup.get(f"{API}/admin/queues").json()
    key = next(q["key"] for q in queues if q["kind"] == "documents" and q.get("waiting", 0) >= 29)
    url = f"{API}/admin/queues/{key}/items"
    first = sup.get(url, params={"state": "waiting"}).json()
    assert first["total"] == 29 and first["page"] == 1 and first["page_size"] == 25 and len(first["items"]) == 25
    second = sup.get(url, params={"state": "waiting", "page": 2}).json()
    titles = [i["title"] for i in first["items"]] + [i["title"] for i in second["items"]]
    assert len(set(titles)) == 29
    assert [i["title"] for i in sup.get(url, params={"state": "waiting", "q": "WAIT05"}).json()["items"]] == ["wait05.pdf"]
    assert [i["title"] for i in sup.get(url, params={"state": "waiting", "q": "user2@"}).json()["items"]] == ["theirs.pdf"]
    assert sup.get(url, params={"state": "failed"}).json()["total"] == 0
    assert s["c"]["admin_a"].get(url).status_code == 403
    assert all(s["db"].get(Job, uuid.UUID(i["job_id"])).status == "queued" for i in first["items"])
