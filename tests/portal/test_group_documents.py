"""A group's documents for its admins: a page at a time, searched, filtered and sorted like a person's own list,
with whose each is. The tab showed only the group's newest 200, with nothing to find one by."""
from __future__ import annotations

from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from tests.portal.test_my_documents import _docs, _names


def _url(s, group="a"):
    return f"{API}/groups/{s[group].id}/documents"


def test_every_document_of_the_group_a_page_at_a_time_with_whose_it_is(setup):
    s = setup
    _docs(s)   # four of user@a.test's and one of user2@a.test's
    admin = s["c"]["admin_a"]
    body = admin.get(_url(s), params={"page_size": 2}).json()
    assert body["total"] == 5 and body["page_size"] == 2 and len(body["items"]) == 2 and body["active"] == 3
    seen = _names(body) + _names(admin.get(_url(s), params={"page_size": 2, "page": 2}).json()) \
        + _names(admin.get(_url(s), params={"page_size": 2, "page": 3}).json())
    assert len(seen) == 5 and len(set(seen)) == 5
    assert {d["user_email"] for d in admin.get(_url(s)).json()["items"]} == {"user@a.test", "user2@a.test"}
    # Cost is a super admin's to see
    assert all(d["cost"] is None for d in admin.get(_url(s)).json()["items"])
    assert s["c"]["super"].get(_url(s)).json()["total"] == 5


def test_search_finds_a_file_or_a_person_and_the_filters_are_the_same(setup):
    s = setup
    _docs(s)
    admin, url = s["c"]["admin_a"], _url(s)
    assert _names(admin.get(url, params={"q": "REPORT"}).json()) == ["Annual report.pdf"]
    assert _names(admin.get(url, params={"q": "user2@"}).json()) == ["someone else's.pdf"]
    assert admin.get(url, params={"q": "a.test"}).json()["total"] == 5
    assert _names(admin.get(url, params={"q": "_2026"}).json()) == ["budget_2026.pdf"]  # "_" is a letter here too
    assert _names(admin.get(url, params={"status": "done"}).json()) == ["Annual report.pdf"]
    assert _names(admin.get(url, params={"status": "failed"}).json()) == ["minutes.pdf"]
    assert _names(admin.get(url, params={"result": "passes"}).json()) == ["Annual report.pdf"]
    assert admin.get(url, params={"service": "standard"}).json()["total"] == 5
    assert admin.get(url, params={"audit": "true"}).json()["total"] == 0
    assert admin.get(url, params={"since": "2999-01-01T00:00:00Z"}).json()["total"] == 0
    assert admin.get(url, params={"status": "lost"}).status_code == 422


def test_sorting_by_person_is_for_this_list_only(setup):
    s = setup
    _docs(s)
    admin, url = s["c"]["admin_a"], _url(s)
    by_person = [d["user_email"] for d in admin.get(url, params={"sort": "-user"}).json()["items"]]
    assert by_person == ["user@a.test"] * 4 + ["user2@a.test"] and by_person == sorted(by_person, reverse=True)
    assert [d["user_email"] for d in admin.get(url, params={"sort": "user"}).json()["items"]][0] == "user2@a.test"
    assert _names(admin.get(url, params={"sort": "-pages"}).json())[0] == "Course catalog.pdf"
    assert admin.get(url, params={"sort": "owner"}).status_code == 422
    # A person's own list has nobody else to sort by, and its search doesn't look at email addresses
    mine = f"{API}/documents"
    assert s["c"]["user"].get(mine, params={"sort": "user"}).status_code == 422
    assert s["c"]["user"].get(mine, params={"q": "user@a.test"}).json()["total"] == 0


def test_only_the_groups_admins_and_super_admins_see_it(setup):
    s = setup
    _docs(s)
    assert s["c"]["user"].get(_url(s)).status_code in (403, 404)   # as for every group page: not theirs, not found
    assert s["c"]["admin_b"].get(_url(s)).status_code in (403, 404)
    assert s["c"]["admin_b"].get(_url(s, "b")).json()["total"] == 0   # their own group has none of these
