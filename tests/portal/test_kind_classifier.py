"""The kind-of-document classifier: "Let AI decide" picks the kind before the document is sent, with Jev for groups
that may send text outside and a local model for local-only ones; General when it isn't sure or can't be used."""
from __future__ import annotations

from portal.engine import AIChoice, EngineUnavailable
from tests.portal.test_ai_levels import _kind
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401


def _settings(s, **body):
    r = s["c"]["super"].patch(f"{API}/admin/settings", json=body)
    assert r.status_code == 200, r.text


def _send(s, **data):
    r = s["c"]["user"].post(f"{API}/jobs", files=[("files", ("lab.pdf", pdf_bytes(2), "application/pdf"))],
                            data=data)
    assert r.status_code == 200 and r.json()[0]["accepted"], r.text
    return r.json()[0]["job"]


def _level(s):
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    _kind(s, level, "Math and science", "local-vision")
    _kind(s, level, "Forms", "local-vision")
    return level


def _offered(s, level):
    return [k["key"] for k in next(x for x in s["c"]["user"].get(f"{API}/services").json()
                                   if x["key"] == level)["kinds"]]


def test_let_ai_decide_is_offered_only_with_a_classifier(setup):
    s = setup
    level = _level(s)
    assert "auto" not in _offered(s, level)
    r = s["c"]["user"].post(f"{API}/jobs", files=[("files", ("a.pdf", pdf_bytes(1), "application/pdf"))],
                            data={"ai_kind": "auto"})
    assert r.json()["detail"]["error"] == "classifier_unavailable"
    _settings(s, classifier_jev=True)
    assert _offered(s, level) == ["general", "auto", "math-and-science", "forms"]
    kinds = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)["kinds"]
    assert "outside service" in kinds[1]["description"]
    # "auto" can't be a kind's own key
    assert s["c"]["super"].post(f"{API}/admin/services/{level}/kinds",
                                json={"label": "Auto", "gateway_model": "x"}).status_code == 409


def test_the_classifier_picks_the_kind(setup):
    s, db = setup, setup["db"]
    level = _level(s)
    _settings(s, classifier_jev=True, classifier_threshold=0.7, classifier_pages=4)
    s["engine"].classify_answer = {"kind": "math-and-science", "confidence": 0.93, "cost_usd": 0.0001}
    job = _send(s, ai_kind="auto")
    assert job["ai_kind_auto"] and job["ai_kind"] is None and job["ai_label"] == "Premium AI · Let AI decide"
    run_worker(db)
    assert s["engine"].classified == [("lab.pdf", ["math-and-science", "forms"], "jev", None, 4)]
    # The kind's model is used
    assert s["engine"].ai_choices[-1] == AIChoice(level, "local-vision", None)
    j = s["c"]["user"].get(f"{API}/jobs/{job['id']}").json()
    assert j["ai_kind"] == "math-and-science" and j["ai_label"] == "Premium AI · Math and science"
    assert j["ai_kind_pick"] == {"kind": "math-and-science", "label": "Math and science", "confidence": 0.93,
                                 "used": True, "by": "jev"}
    assert "classifier" in j["external_services"]


def test_general_when_unsure_or_unavailable(setup):
    s, db = setup, setup["db"]
    level = _level(s)
    _settings(s, classifier_jev=True, classifier_threshold=0.8)
    s["engine"].classify_answer = {"kind": "forms", "confidence": 0.55}
    unsure = _send(s, ai_kind="auto")
    run_worker(db)
    j = s["c"]["user"].get(f"{API}/jobs/{unsure['id']}").json()
    assert j["ai_kind"] is None and j["ai_kind_pick"]["reason"] == "unsure" and not j["ai_kind_pick"]["used"]
    assert j["ai_kind_pick"]["label"] == "Forms" and j["status"] in ("submitted", "processing")
    assert s["engine"].ai_choices[-1] == AIChoice(level, "commercial", None)
    # A scan with no text
    s["engine"].classify_answer = {"kind": None, "confidence": None, "reason": "no_text"}
    scan = _send(s, ai_kind="auto")
    # The classifier can't be reached: the document isn't held up
    run_worker(db)
    assert s["c"]["user"].get(f"{API}/jobs/{scan['id']}").json()["ai_kind_pick"]["reason"] == "no_text"
    s["engine"].classify_answer = EngineUnavailable("down")
    down = _send(s, ai_kind="auto")
    run_worker(db)
    j = s["c"]["user"].get(f"{API}/jobs/{down['id']}").json()
    assert j["status"] in ("submitted", "processing") and j["ai_kind_pick"]["reason"] == "failed" and j["ai_kind"] is None
    assert "classifier" not in j["external_services"]


def test_local_only_groups_use_the_local_model(setup):
    s, db = setup, setup["db"]
    level = _level(s)
    _settings(s, classifier_jev=True)
    assert s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"local_only": True}).status_code == 200
    assert "auto" not in _offered(s, level)  # Jev is external, and there's no local model
    _settings(s, classifier_local_model="local-vision")
    kinds = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)["kinds"]
    assert kinds[1]["key"] == "auto" and "outside" not in kinds[1]["description"]
    s["engine"].classify_answer = {"kind": "forms", "confidence": 0.9}
    job = _send(s, ai_kind="auto")
    db.expire_all()  # the group changed through the API (the worker reads it fresh each round)
    run_worker(db)
    assert s["engine"].classified[-1][2:4] == ("gateway", "local-vision")
    j = s["c"]["user"].get(f"{API}/jobs/{job['id']}").json()
    assert j["ai_kind"] == "forms" and "classifier" not in j["external_services"]


def test_a_group_can_let_ai_decide_for_everyone(setup):
    s, db = setup, setup["db"]
    level = _level(s)
    patch = lambda **b: s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json=b)  # noqa: E731
    assert patch(ai_mode="fixed", ai_level=level, ai_kind="auto").json()["detail"]["error"] == "classifier_unavailable"
    _settings(s, classifier_jev=True)
    gs = next(x for x in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()["services"] if x["key"] == level)
    assert gs["kinds"][0] == {"key": "auto", "label": "Let AI decide"}
    assert patch(ai_mode="fixed", ai_level=level, ai_kind="auto").status_code == 200
    s["engine"].classify_answer = {"kind": "forms", "confidence": 0.99}
    job = _send(s, ai_kind="general")
    assert job["ai_kind_auto"]
    run_worker(db)
    assert s["c"]["user"].get(f"{API}/jobs/{job['id']}").json()["ai_kind"] == "forms"
