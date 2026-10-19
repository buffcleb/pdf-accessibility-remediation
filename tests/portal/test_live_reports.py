"""#12 part A: live reports — filter, search, sort, page, totals, headline numbers, and exporting what's shown."""
from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy import select

from portal.models import AuditEvent, Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

REPORTS = ["documents", "usage", "ledger", "sign_ins", "help", "audit", "quality", "failures", "ai_usage"]
SUPER_ONLY = ["cost", "daily", "report_emails", "limits", "describe"]


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
    assert {h["label"]: h["value"] for h in body["headline"]}["Pass PDF/UA"] == "67%"

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


@pytest.mark.parametrize("name", ["quality", "failures", "cost", "daily", "ai_usage", "limits", "describe"])
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


def _ai_jobs(s):
    """Two documents on the Local AI level (one served by a different model), one without AI."""
    db = s["db"]
    r = s["c"]["super"].post(f"{API}/admin/services", json={"label": "Local AI", "gateway_model": "local-vision"})
    assert r.status_code == 201, r.text
    level = r.json()["key"]
    jobs = list(db.scalars(select(Job).order_by(Job.created_at)))
    use = {"ai_calls": 4, "ai_failures": 1, "prompt_tokens": 1000, "completion_tokens": 200,
           "review_prompt_tokens": 300, "review_completion_tokens": 50, "writer_model": "qwen-vl",
           "review_model": "gemma", "review_usd": 0.01, "seconds": 30}
    jobs[0].ai_tier, jobs[0].cost = level, {**use, "ai_usd": 0.05}
    jobs[1].ai_tier, jobs[1].cost = level, {**use, "ai_usd": 0.15, "writer_model": "other-vl", "ai_refunded": True,
                                                 "classifier_usd": 0.0001}
    jobs[2].cost = {"ai_usd": 0, "ai_calls": 0}
    db.commit()
    return level


def test_ai_usage_by_level_and_model(data):
    s = data
    level = _ai_jobs(s)
    body = _get(s, "super", "ai_usage")
    rows = body["rows"]
    assert {r["writer_model"] for r in rows} == {"qwen-vl", "other-vl"}   # one row per model
    assert all(r["level"] == "Local AI" for r in rows), rows              # documents without AI aren't in it
    t = body["totals"]
    assert t["documents"] == 2 and t["calls"] == 8 and t["failed"] == 2 and t["refunded"] == 1
    assert t["tokens"] == 2 * 1550 and t["ai_usd"] == 0.2 and t["measured"] == 2
    head = {h["label"]: h for h in body["headline"]}
    assert head["AI calls"]["hint"] == "2 failed" and head["AI cost"]["value"] == "$0.20"
    assert {m["key"] for m in body["chart"]["measures"]} == {"ai_usd", "calls", "tokens", "documents"}
    # Filters: the AI level, and audits
    assert _get(s, "super", "ai_usage", level=level)["totals"]["documents"] == 2
    assert _get(s, "super", "ai_usage", audit="yes")["totals"].get("documents", 0) == 0


def test_group_admins_see_ai_usage_without_cost_or_models(data):
    s = data
    _ai_jobs(s)
    body = _get(s, "admin_a", "ai_usage")
    keys = {c["key"] for c in body["columns"]}
    assert not keys & {"ai_usd", "usd_per_doc", "usd_per_page", "writer_model", "review_model", "classifier_usd"}
    assert len(body["rows"]) == 1 and body["rows"][0]["documents"] == 2  # grouped by level only: no model rows
    assert "AI cost" not in {h["label"] for h in body["headline"]}
    assert {m["key"] for m in body["chart"]["measures"]} == {"calls", "tokens", "documents"}


def test_the_worker_keeps_what_the_ai_did():
    from types import SimpleNamespace

    from portal import worker
    job = SimpleNamespace(ai_tier="ai:local", ai_model="fallback-model")
    m = {"ai_tokens_used": {"prompt_tokens": 10, "completion_tokens": 5}, "ai_cost_usd": 0.02, "ai_failures": 1,
         "ai_model": "qwen-vl", "verification_ai_tokens": {"prompt_tokens": 3, "completion_tokens": 1},
         "verification_ai_cost_usd": 0.01, "processing_time_seconds": 12.5}
    worker_judge = worker._judge_for
    worker._judge_for = lambda db, j: "gemma"
    try:
        use = worker._ai_use(None, job, m)
    finally:
        worker._judge_for = worker_judge
    assert use == {"writer_usd": 0.02, "review_usd": 0.01, "prompt_tokens": 10, "completion_tokens": 5,
                   "review_prompt_tokens": 3, "review_completion_tokens": 1, "ai_failures": 1,
                   "writer_model": "qwen-vl", "review_model": "gemma", "seconds": 12.5}
    # No AI level, but the engine's own default AI was used: recorded all the same (no level's review model)
    default = worker._ai_use(None, SimpleNamespace(ai_tier=None, ai_model=None), m)
    assert default["prompt_tokens"] == 10 and default["writer_model"] == "qwen-vl" and default["review_model"] is None
    assert worker._ai_use(None, SimpleNamespace(ai_tier=None, ai_model=None),
                          {"ai_calls": 0, "processing_time_seconds": 3.0}) == {"seconds": 3.0}


def test_documents_on_the_engines_default_ai_are_counted(data):
    """Sent without an AI level, a document uses the engine's own default AI: it's counted under its own row."""
    s = data
    db = s["db"]
    jobs = list(db.scalars(select(Job).order_by(Job.created_at)))
    jobs[0].cost = {"ai_usd": 0, "ai_calls": 153, "ai_failures": 0, "writer_model": "qwen3-vl-8b-instruct"}
    for j in jobs[1:]:
        j.cost = {"ai_usd": 0, "ai_calls": 0}  # no AI at all: not in the report
    db.commit()
    body = _get(s, "super", "ai_usage")
    assert [(r["level"], r["writer_model"], r["calls"]) for r in body["rows"]] == \
        [("Engine default AI (no AI level)", "qwen3-vl-8b-instruct", 153)]
    assert _get(s, "super", "ai_usage", level="default")["totals"]["documents"] == 1
    assert "default" in [o["value"] for f in body["filters"] if f["key"] == "level" for o in f["options"]]


def test_limits_hit_are_seen_by_document_and_by_limit(data):
    """#312: which documents reached a limit, and which limits are reached how often and by how much."""
    s = data
    db = s["db"]

    def hit(name, *limits, error=None):
        job = db.scalars(select(Job).where(Job.filename == name)).one()
        job.result_summary = {**job.result_summary, "metrics": {**(job.result_summary.get("metrics") or {}), "limits_hit": [
            {"setting": k, "value": v, "wanted": w, "effect": f"{k} effect"} for k, v, w in limits]}}
        if error:
            job.status, job.error_code = "failed", error
        db.commit()
    hit("annual-report.pdf", ("AI_NOTATION_MAX", 100, 140), ("VERIFY_AI_MAX_ITEMS", 150, 210))
    hit("syllabus.pdf", ("AI_NOTATION_MAX", 100, 260))
    hit("minutes.pdf", error="time_limit_exceeded")

    docs = _get(s, "super", "documents", columns="filename,limits", limits="yes")
    got = {r["filename"]: r["limits"] for r in docs["rows"]}
    assert got == {"annual-report.pdf": "AI_NOTATION_MAX, VERIFY_AI_MAX_ITEMS", "syllabus.pdf": "AI_NOTATION_MAX",
                   "minutes.pdf": "TASK_TIME_LIMIT_S"}
    assert _get(s, "super", "documents", limits="no")["total_rows"] == 0

    rows = {r["setting"]: r for r in _get(s, "super", "limits")["rows"]}
    assert set(rows) == {"AI_NOTATION_MAX", "VERIFY_AI_MAX_ITEMS"}
    assert (rows["AI_NOTATION_MAX"]["documents"], rows["AI_NOTATION_MAX"]["wanted"], rows["AI_NOTATION_MAX"]["value"]) \
        == (2, 260, 100)
    # The column is super admins' (adjusting limits is theirs)
    assert "limits" not in [c["key"] for c in _get(s, "admin_a", "documents")["columns"]]


def test_the_portal_adds_its_own_review_list_limit():
    from portal.worker import _limits_hit
    from portal.config import get_portal_settings

    kept = get_portal_settings().review_findings_max
    engine = [{"setting": "AI_IMAGES_MAX", "value": 3, "wanted": 5, "effect": "e"}]
    assert _limits_hit({"limits_hit": engine}, kept) == engine
    out = _limits_hit({"limits_hit": engine}, kept + 7)
    assert out[-1]["setting"] == "PORTAL_REVIEW_FINDINGS_MAX" and out[-1]["wanted"] == kept + 7
    assert _limits_hit({}, 0) == []


def test_describe_with_ai_is_counted_per_use_by_person_and_cost(data):
    """#318: how much Describe with AI is used and what it costs, to size recharging it (#317)."""
    from datetime import datetime, timezone

    s = data
    db = s["db"]
    from portal.models import User
    jordan = db.scalars(select(User).where(User.email == "user@a.test")).first() or db.scalars(select(User)).first()
    now = datetime.now(timezone.utc).isoformat()
    job = db.scalars(select(Job).where(Job.filename == "annual-report.pdf")).one()
    job.cost = {**(job.cost or {}), "describe_usd": 0.003, "describe_calls": 3, "describe_log": [
        {"at": now, "user": str(jordan.id), "usd": 0.001, "model": "m", "page": 1} for _ in range(3)]}
    other = db.scalars(select(Job).where(Job.filename == "minutes.pdf")).one()
    other.cost = {**(other.cost or {}), "describe_log": None}       # a JSON null: counted as none
    db.commit()
    body = _get(s, "super", "describe", by="month")
    assert body["total_rows"] == 1
    row = body["rows"][0]
    assert row["uses"] == 3 and row["documents"] == 1 and abs(row["usd"] - 0.003) < 1e-9 and row["person"] == jordan.email
    head = {h["label"]: h["value"] for h in body["headline"]}
    assert head["Uses"] == 3 and head["Cost"] == "$0.00" and head["People"] == 1
    # ...and the cost report can show it beside the AI cost, for super admins
    cost = _get(s, "super", "cost", columns="period,describe_usd")
    assert abs(sum(r.get("describe_usd") or 0 for r in cost["rows"]) - 0.003) < 1e-9

