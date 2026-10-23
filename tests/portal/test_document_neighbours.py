"""#482: a document's Previous and Next follow the list it was opened from (My documents, a group's documents, the
Documents report), in its order and with its filters, across its pages, and only through documents the person can see."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from portal.models import Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from tests.portal.test_my_documents import _docs, _names

MINE = f"{API}/documents"


def test_previous_and_next_follow_my_documents_sort_and_filters(setup):
    s = setup
    ids = _docs(s)
    c = s["c"]["user"]
    in_order = _names(c.get(MINE, params={"sort": "filename"}).json())
    assert in_order == ["Annual report.pdf", "budget_2026.pdf", "Course catalog.pdf", "minutes.pdf"]
    n = c.get(f"{MINE}/neighbours/{ids['budget_2026.pdf']}", params={"sort": "filename"}).json()
    assert n["position"] == 2 and n["total"] == 4
    assert n["previous"] == {"id": ids["Annual report.pdf"], "filename": "Annual report.pdf"}
    assert n["next"] == {"id": ids["Course catalog.pdf"], "filename": "Course catalog.pdf"}
    # The other way round
    n = c.get(f"{MINE}/neighbours/{ids['budget_2026.pdf']}", params={"sort": "-filename"}).json()
    assert (n["previous"]["filename"], n["next"]["filename"], n["position"]) == ("Course catalog.pdf", "Annual report.pdf", 3)
    # The ends have nothing beyond them
    first = c.get(f"{MINE}/neighbours/{ids['Annual report.pdf']}", params={"sort": "filename"}).json()
    assert first["previous"] is None and first["position"] == 1
    last = c.get(f"{MINE}/neighbours/{ids['minutes.pdf']}", params={"sort": "filename"}).json()
    assert last["next"] is None and last["position"] == 4
    # Filtered: only what the list showed (the two still in progress), the rest skipped
    n = c.get(f"{MINE}/neighbours/{ids['budget_2026.pdf']}", params={"sort": "filename", "status": "in_progress"}).json()
    assert n["total"] == 2 and n["previous"] is None and n["next"]["filename"] == "Course catalog.pdf"
    # A document that no longer matches the filters has no place in the list
    n = c.get(f"{MINE}/neighbours/{ids['minutes.pdf']}", params={"status": "in_progress"}).json()
    assert n == {"position": None, "total": 2, "previous": None, "next": None}
    assert c.get(f"{MINE}/neighbours/{ids['minutes.pdf']}", params={"sort": "owner"}).status_code == 422


def test_the_order_is_the_lists_across_its_pages(setup):
    s = setup
    _docs(s)
    c = s["c"]["user"]
    # Uploaded together, the documents share a time: the order still has to be one order, page after page
    db = s["db"]
    same = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for j in db.query(Job).all():
        j.created_at = same
    db.commit()
    paged = [d["id"] for page in (1, 2) for d in c.get(MINE, params={"page_size": 2, "page": page}).json()["items"]]
    assert len(set(paged)) == 4
    # Next from the first goes through every document in the list's order, from page 1 onto page 2
    seen, at = [], paged[0]
    while at:
        seen.append(at)
        at = (c.get(f"{MINE}/neighbours/{at}").json()["next"] or {}).get("id")
    assert seen == paged
    assert [c.get(f"{MINE}/neighbours/{i}").json()["position"] for i in paged] == [1, 2, 3, 4]


def test_only_documents_the_person_can_see(setup):
    s = setup
    ids = _docs(s)
    someone_elses = s["c"]["user2"].get(MINE).json()["items"][0]["id"]
    # Their own list never holds someone else's document, so it has no neighbours there
    n = s["c"]["user"].get(f"{MINE}/neighbours/{someone_elses}").json()
    assert n["position"] is None and n["total"] == 4
    # A group's documents, for its admins: everyone's in the group, in that list's order
    group = f"{API}/groups/{s['a'].id}/documents"
    admin = s["c"]["admin_a"]
    every = _names(admin.get(group, params={"sort": "filename"}).json())
    walked = []
    at = admin.get(group, params={"sort": "filename"}).json()["items"][0]["id"]
    while at:
        n = admin.get(f"{group}/neighbours/{at}", params={"sort": "filename"}).json()
        walked.append(n["next"]["filename"] if n["next"] else None)
        at = n["next"] and n["next"]["id"]
    assert walked == every[1:] + [None] and len(every) == 5
    # Search by person, as the list does
    n = admin.get(f"{group}/neighbours/{someone_elses}", params={"q": "user2@"}).json()
    assert n["total"] == 1 and n["position"] == 1
    # Another group's admin and the group's members don't get the group's list
    assert s["c"]["admin_b"].get(f"{group}/neighbours/{someone_elses}").status_code in (403, 404)
    assert s["c"]["user"].get(f"{group}/neighbours/{ids['minutes.pdf']}").status_code in (403, 404)
    # A deleted document leaves every list (super admins still open it, but it isn't listed)
    db = s["db"]
    db.get(Job, uuid.UUID(ids["budget_2026.pdf"])).deleted_at = datetime.now(timezone.utc)
    db.commit()
    n = s["c"]["user"].get(f"{MINE}/neighbours/{ids['Annual report.pdf']}", params={"sort": "filename"}).json()
    assert n["next"]["filename"] == "Course catalog.pdf" and n["total"] == 3


def test_the_documents_report_has_previous_and_next_too(setup):
    s = setup
    ids = _docs(s)
    report = f"{API}/reports/documents"
    rows = s["c"]["super"].get(report, params={"sort": "filename"}).json()["rows"]
    in_order = [r["filename"] for r in rows]
    walked, at = [], rows[0]["_job_id"]
    while at:
        n = s["c"]["super"].get(f"{report}/neighbours/{at}", params={"sort": "filename"}).json()
        walked.append(n["position"])
        at = n["next"] and n["next"]["id"]
    assert walked == list(range(1, len(in_order) + 1))
    # The report's own filters and search
    n = s["c"]["super"].get(f"{report}/neighbours/{ids['minutes.pdf']}", params={"status": "failed"}).json()
    assert (n["position"], n["total"]) == (1, 1)
    n = s["c"]["super"].get(f"{report}/neighbours/{ids['budget_2026.pdf']}", params={"q": "user2@", "sort": "filename"}).json()
    assert n["position"] is None and n["total"] == 1
    # A group admin: their group's documents, never a deleted one; another group's admin sees none of these
    db = s["db"]
    db.get(Job, uuid.UUID(ids["budget_2026.pdf"])).deleted_at = datetime.now(timezone.utc)
    db.commit()
    n = s["c"]["admin_a"].get(f"{report}/neighbours/{ids['Course catalog.pdf']}", params={"sort": "filename"}).json()
    assert n["next"]["filename"] == "minutes.pdf" and n["total"] == 4   # (the report's sort minds case: C before b)
    n = s["c"]["super"].get(f"{report}/neighbours/{ids['Course catalog.pdf']}", params={"sort": "filename"}).json()
    assert n["next"]["filename"] == "budget_2026.pdf" and n["total"] == 5
    assert s["c"]["admin_b"].get(f"{report}/neighbours/{ids['Annual report.pdf']}").json()["position"] is None
    # A report that doesn't list documents has no neighbours to give
    assert s["c"]["super"].get(f"{API}/reports/usage/neighbours/{ids['minutes.pdf']}").status_code == 404
    assert s["c"]["user"].get(f"{report}/neighbours/{ids['minutes.pdf']}").status_code in (403, 404)
