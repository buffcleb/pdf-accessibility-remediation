"""#12 part A: live reports — filter, search, sort, page, totals, headline numbers, and exporting what's shown."""
from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy import select

from portal.models import AuditEvent, Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

REPORTS = ["documents", "usage", "ledger", "sign_ins", "help", "audit", "quality", "failures"]
SUPER_ONLY = ["cost", "daily", "report_emails"]


def _done(s, client, name, pages):
    job_id = upload(s["c"][client], (name, pdf_bytes(pages))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    return job_id


@pytest.fixture
def data(setup):
    s = setup
    topup(s, s["a"].id, 100, ticket_ref="RITM0000001")
    topup(s, s["b"].id, 50, ticket_ref="RITM0000002")
    _done(s, "user", "annual-report.pdf", 3)
    _done(s, "user", "syllabus.pdf", 2)
    _done(s, "user2", "minutes.pdf", 1)
    return s


def _get(s, client, name, **params):
    r = s["c"][client].get(f"{API}/reports/{name}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("name", REPORTS)
def test_every_report_runs_for_super_and_group_admins(data, name):
    for client in ("super", "admin_a"):
        body = _get(data, client, name)
        assert body["name"] == name and isinstance(body["rows"], list) and body["columns"]
        assert body["total_rows"] >= len(body["rows"])
        _get(data, client, name, q="x", sort=f"-{body['columns'][0]['key']}", page_size=5)


def test_documents_filter_search_sort_page_and_totals(data):
    s = data
    body = _get(s, "super", "documents")
    assert body["total_rows"] == 3 and body["totals"]["pages_charged"] == 6
    labels = {h["label"]: h["value"] for h in body["headline"]}
    assert labels["Documents"] == 3 and labels["Pages charged"] == 6

    assert [r["filename"] for r in _get(s, "super", "documents", q="SYLLA")["rows"]] == ["syllabus.pdf"]
    by_pages = _get(s, "super", "documents", sort="-pages")["rows"]
    assert [r["pages"] for r in by_pages] == [3, 2, 1]
    page2 = _get(s, "super", "documents", sort="pages", page=2, page_size=1)
    assert page2["total_rows"] == 3 and [r["pages"] for r in page2["rows"]] == [2]
    assert _get(s, "super", "documents", status="failed")["total_rows"] == 0
    assert _get(s, "super", "documents", outcome="pass")["total_rows"] == 3
    row = by_pages[0]
    assert row["_job_id"] and row["_group_id"] == str(s["a"].id)


def test_group_admins_see_their_group_and_no_costs(data):
    s = data
    mine = _get(s, "admin_a", "documents")
    assert mine["total_rows"] == 3 and "ai_usd" not in [c["key"] for c in mine["columns"]]
    assert "ai_usd" in [c["key"] for c in _get(s, "super", "documents")["columns"]]
    assert _get(s, "admin_b", "documents")["total_rows"] == 0
    other = s["c"]["admin_b"].get(f"{API}/reports/documents", params={"group_id": str(s["a"].id)})
    assert other.status_code == 404
    assert s["c"]["user"].get(f"{API}/reports/documents").status_code == 403
    assert s["c"]["super"].get(f"{API}/reports/nothing").status_code == 404
    ledger_b = _get(s, "super", "ledger", group_id=str(s["b"].id))
    assert {r["group"] for r in ledger_b["rows"]} == {"Campus B"}


def test_usage_counts_documents_in_the_period(data):
    s = data
    rows = _get(s, "admin_a", "usage")["rows"]
    user = next(r for r in rows if r["person"] == "user@a.test" and r["service"] == "Standard")
    assert user["used"] == 5 and user["docs_period"] == 2 and user["pages_period"] == 5
    future = _get(s, "admin_a", "usage", since="2099-01-01T00:00:00Z")["rows"]
    assert next(r for r in future if r["person"] == "user@a.test")["docs_period"] == 0


def test_ledger_search_by_ticket_and_filter_by_kind(data):
    s = data
    hits = _get(s, "super", "ledger", q="RITM0000002")
    assert hits["total_rows"] == 1 and hits["rows"][0]["group"] == "Campus B"
    topups = _get(s, "super", "ledger", kind="topup")
    assert topups["totals"]["pages"] == 150
    labels = {h["label"]: h["value"] for h in topups["headline"]}
    assert labels["Pages added"] == 150


def test_export_is_what_you_see_and_is_audited(data):
    s = data
    r = s["c"]["admin_a"].get(f"{API}/reports/documents.csv",
                              params={"q": "pdf", "sort": "-pages", "cols": "filename,pages", "status": "completed"})
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["Document", "Pages"] and [x[1] for x in rows[1:]] == ["3", "2", "1"]
    event = s["db"].scalars(select(AuditEvent).where(AuditEvent.action == "report.exported")
                            .order_by(AuditEvent.id.desc())).first()
    assert event.details["report"] == "documents" and event.details["rows"] == 3
    assert event.details["filters"] == {"status": "completed"} and event.details["scope"] == "their groups"


def test_chosen_columns_are_marked_shown(data):
    body = _get(data, "super", "documents", cols="filename,person")
    shown = [c["key"] for c in body["columns"] if c["shown"]]
    assert shown == ["filename", "person"]


# --- part B: quality, remaining failures, cost, daily use ---------------------------------------------

@pytest.mark.parametrize("name", SUPER_ONLY)
def test_super_admin_only_reports(data, name):
    body = _get(data, "super", name)
    assert body["name"] == name and (body["chart"] is not None or name == "report_emails")  # a list, no chart
    assert data["c"]["admin_a"].get(f"{API}/reports/{name}").status_code == 404
    assert name not in [r["name"] for r in data["c"]["admin_a"].get(f"{API}/reports").json()]
    assert name in [r["name"] for r in data["c"]["super"].get(f"{API}/reports").json()]


def test_quality_pass_rate_by_period_and_option(data):
    s = data
    db = s["db"]
    one = db.scalars(select(Job).where(Job.filename == "minutes.pdf")).one()
    one.result_summary = {**one.result_summary, "compliant_after": False, "failed_checks_after": 4,
                          "remaining_failures": [{"clause": "7.1-3", "description": "Content not tagged", "failed_checks": 4}]}
    db.commit()
    body = _get(s, "super", "quality", by="month")
    assert body["period"] == "month" and body["total_rows"] == 1
    row = body["rows"][0]
    assert row["option"] == "Standard" and row["documents"] == 3 and row["passed"] == 2 and row["pass_rate"] == 66.7
    chart = body["chart"]
    assert chart["kind"] == "line" and chart["series"] == "option"
    assert round(chart["points"][0]["pass_rate"], 1) == 66.7  # a rate of the sums, not a sum of rates
    assert {h["label"]: h["value"] for h in body["headline"]}["Pass PDF/UA-1"] == "67%"

    failures = _get(s, "admin_a", "failures")
    assert failures["rows"][0]["clause"] == "7.1-3" and failures["rows"][0]["documents"] == 1
    assert failures["chart"]["kind"] == "hbar"
    assert _get(s, "admin_b", "failures")["total_rows"] == 0  # another group's documents


def test_period_follows_the_date_range_unless_chosen():
    from datetime import datetime, timedelta, timezone

    from portal.live_reports import Query, period_for
    now = datetime.now(timezone.utc)
    assert period_for(Query()) == "month"  # all time
    assert period_for(Query(since=now - timedelta(days=1))) == "hour"
    assert period_for(Query(since=now - timedelta(days=30))) == "day"
    assert period_for(Query(since=now - timedelta(days=200))) == "week"
    assert period_for(Query(since=now - timedelta(days=800))) == "month"
    assert period_for(Query(since=now - timedelta(days=30), filters={"by": "month"})) == "month"
    assert period_for(Query(filters={"by": "fortnight"})) == "month"  # unknown: the default


def test_quality_by_hour(data):
    body = _get(data, "super", "quality", by="hour")
    assert body["period"] == "hour" and body["total_rows"] >= 1
    assert body["chart"]["points"]


def test_cost_and_daily_use(data):
    s = data
    db = s["db"]
    for j in db.scalars(select(Job)):
        j.cost = {"ai_usd": 0.25}
    db.commit()
    cost = _get(s, "super", "cost")
    assert cost["totals"]["ai_usd"] == 0.75 and cost["totals"]["pages"] == 6
    assert [m["key"] for m in cost["chart"]["measures"]] == ["ai_usd", "adobe_pages", "pages"]

    from portal.models import Service
    db.get(Service, "standard").daily_cap_pages = 10
    db.commit()
    daily = _get(s, "super", "daily")
    row = daily["rows"][0]
    assert row["service"] == "Standard" and row["pages"] == 6 and row["cap"] == 10 and row["pct"] == 60
    assert daily["chart"]["ref"] is True and daily["chart"]["points"][0]["_ref"] == 10


@pytest.mark.parametrize("name", ["quality", "failures", "cost", "daily"])
def test_grouped_reports_sort_by_every_column(data, name):
    cols = _get(data, "super", name)["columns"]
    for c in cols:
        for sort in (c["key"], f"-{c['key']}"):
            _get(data, "super", name, sort=sort, by="week")



def test_group_admins_never_see_cost(data):
    """What groups are charged isn't the per-page cost, so cost stays with super admins: no Cost report, no cost
    columns, and no cost on a document."""
    s = data
    db = s["db"]
    for j in db.scalars(select(Job)):
        j.cost = {"ai_usd": 0.25}
    db.commit()
    job_id = db.scalars(select(Job)).first().id
    admin = s["c"]["admin_a"]
    assert all(r["name"] not in SUPER_ONLY for r in admin.get(f"{API}/reports").json())
    assert all(c["key"] != "ai_usd" for c in _get(s, "admin_a", "documents")["columns"])
    assert all(j.get("cost") is None for j in admin.get(f"{API}/groups/{s['a'].id}/jobs").json())
    assert admin.get(f"{API}/jobs/{job_id}").json().get("cost") is None
    assert s["c"]["super"].get(f"{API}/jobs/{job_id}").json()["cost"] == {"ai_usd": 0.25}
