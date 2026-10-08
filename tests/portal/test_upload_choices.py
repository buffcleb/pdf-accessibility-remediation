"""Upload choices: a group can set one AI level for everything, files can have their own level and kind, and
an audit follows its AI level's setting (free, counted against the level, or made without AI)."""
from __future__ import annotations

import json

from tests.portal.test_ai_levels import _kind
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401


def _post(s, n=1, who="user", **data):
    files = [("files", (f"d{i}.pdf", pdf_bytes(2), "application/pdf")) for i in range(n)]
    return s["c"][who].post(f"{API}/jobs", files=files, data={k: v for k, v in data.items() if v is not None})


def _jobs(s, n=1, **data):
    r = _post(s, n, **data)
    assert r.status_code == 200, r.text
    assert all(x["accepted"] for x in r.json()), r.text
    return [x["job"] for x in r.json()]


def _group(s, **body):
    return s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json=body)


def test_a_group_sets_one_ai_level_for_everything(setup):
    s = setup
    topup(s, s["a"].id, 50)
    basic = _tier(s, "Basic AI", "local-vision", default=True, metered=False)
    premium = _tier(s, "Premium AI", "commercial", metered=False)
    _kind(s, premium, "Math and science", "local-vision")
    assert s["c"]["user"].get(f"{API}/upload-options").json() == {
        "ai_mode": "choose", "ai_level": None, "ai_kind": None, "show_external": False}
    # The level has to be one the group uses, and the kind one the level offers
    assert _group(s, ai_mode="fixed").json()["detail"]["error"] == "level_required"
    assert _group(s, ai_mode="fixed", ai_level=premium, ai_kind="forms").json()["detail"]["error"] == "unknown_kind"
    r = _group(s, ai_mode="fixed", ai_level=premium, ai_kind="math-and-science", show_external=True)
    assert r.status_code == 200, r.text
    assert r.json()["ai_mode"] == "fixed" and r.json()["show_external"] is True
    assert s["c"]["user"].get(f"{API}/upload-options").json() == {
        "ai_mode": "fixed", "ai_level": premium, "ai_kind": "math-and-science", "show_external": True}
    # What people ask for is set aside
    job = _jobs(s, ai_tier=basic, ai_kind="general")[0]
    assert job["ai_tier"] == premium and job["ai_kind"] == "math-and-science"
    assert s["c"]["user"].patch(f"{API}/groups/{s['a'].id}", json={"ai_mode": "choose"}).status_code in (403, 404)
    _group(s, ai_mode="choose")
    assert _jobs(s, ai_tier=basic)[0]["ai_tier"] == basic


def test_files_can_have_their_own_level_and_kind(setup):
    s = setup
    topup(s, s["a"].id, 50)
    basic = _tier(s, "Basic AI", "local-vision", default=True, metered=False)
    premium = _tier(s, "Premium AI", "commercial", metered=False)
    _kind(s, premium, "Math and science", "local-vision")
    per_file = json.dumps({"1": {"ai_tier": premium, "ai_kind": "math-and-science"}})
    jobs = _jobs(s, 3, ai_tier=basic, per_file=per_file)
    assert [(j["ai_tier"], j["ai_kind"]) for j in jobs] == [
        (basic, None), (premium, "math-and-science"), (basic, None)]
    # A bad choice refuses the whole upload before any file is taken
    for bad in ["{", '{"5": {}}', '{"0": {"model": "x"}}', json.dumps({"0": {"ai_tier": premium,
                                                                               "ai_kind": "forms"}})]:
        r = _post(s, 2, per_file=bad)
        assert r.status_code == 422, (bad, r.text)
    assert len(s["c"]["user"].get(f"{API}/jobs").json()) == 3


def test_an_audit_follows_its_levels_setting(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, pages=10)
    offered = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)
    assert offered["audit_ai"] == "free"
    sup = s["c"]["super"]

    free = _jobs(s, report_only="true")[0]  # as before: free
    assert free["pages_charged"] == 0 and free["ai_tier"] == level and not free["no_ai"]

    assert sup.patch(f"{API}/admin/services/{level}", json={"audit_ai": "charge"}).status_code == 200
    charged = _jobs(s, report_only="true")[0]  # the level's pages, not the processing ones
    assert charged["pages_charged"] == 2 and charged["charged_services"] == [level]
    assert next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)[
        "remaining_pages"] == 8

    assert sup.patch(f"{API}/admin/services/{level}", json={"audit_ai": "off"}).status_code == 200
    plain = _jobs(s, report_only="true", full_review="true")[0]
    assert plain["pages_charged"] == 0 and plain["ai_tier"] is None and plain["no_ai"] is True
    assert plain["ai_label"] == "No AI" and plain["full_review"] is False
    run_worker(db)
    assert s["engine"].no_ais == [False, False, True]
    # Remediating isn't affected
    assert _jobs(s)[0]["ai_tier"] == level

    assert sup.patch(f"{API}/admin/services/{level}", json={"audit_ai": "sometimes"}).status_code == 422
    assert sup.patch(f"{API}/admin/services/standard", json={"audit_ai": "off"}).status_code == 422
