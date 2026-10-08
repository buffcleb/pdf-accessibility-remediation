"""AI levels and kinds of document: people choose a level and a kind, never a model; the kind's models are used,
falling back to the level's General models when they aren't available or fail."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal import worker
from portal.engine import AIChoice
from portal.models import AuditEvent, Job
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401


def _kind(s, level, label, model, judge=None):
    r = s["c"]["super"].post(f"{API}/admin/services/{level}/kinds",
                             json={"label": label, "gateway_model": model, "gateway_judge_model": judge,
                                   "description": f"Best for {label.lower()}."})
    assert r.status_code == 201, r.text
    return next(k for k in r.json()["kinds"] if k["label"] == label)


def _post(s, ai_kind=None, name="doc.pdf", who="user"):
    data = {"ai_kind": ai_kind} if ai_kind else {}
    return s["c"][who].post(f"{API}/jobs", files=[("files", (name, pdf_bytes(2), "application/pdf"))], data=data)


def _send(s, ai_kind=None, name="doc.pdf", who="user"):
    r = _post(s, ai_kind, name, who)
    assert r.status_code == 200, r.text
    return r.json()[0]


def _fresh_models(monkeypatch, models):
    monkeypatch.setattr(worker, "_models_seen", (0.0, None))
    monkeypatch.setattr(worker, "_gateway_models", lambda: set(models) if models is not None else None)


def test_kinds_are_offered_without_model_names(setup):
    s = setup
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    k = _kind(s, level, "Math and science", "local-vision", judge="commercial")
    assert k["key"] == "math-and-science" and k["gateway_model"] == "local-vision"  # super admins see models
    assert s["c"]["super"].post(f"{API}/admin/services/{level}/kinds",
                                json={"label": "Math and science", "gateway_model": "x"}).status_code == 409
    assert s["c"]["super"].post(f"{API}/admin/services/{level}/kinds",
                                json={"label": "General", "gateway_model": "x"}).status_code == 409
    assert s["c"]["admin_a"].post(f"{API}/admin/services/{level}/kinds",
                                  json={"label": "Forms", "gateway_model": "x"}).status_code == 403
    offered = next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)
    assert offered["kinds"] == [{"key": "general", "label": "General", "description": None},
                                {"key": "math-and-science", "label": "Math and science",
                                 "description": "Best for math and science."}]
    assert "local-vision" not in str(offered)


def test_the_kinds_models_are_used(setup, monkeypatch):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False, judge="commercial-judge")
    _kind(s, level, "Math and science", "local-vision")
    _fresh_models(monkeypatch, ["commercial", "local-vision"])
    r = _send(s, "math-and-science")
    assert r["accepted"] and r["job"]["ai_kind"] == "math-and-science"
    run_worker(db)
    # The kind's model, and the level's reviewer since the kind has none of its own
    assert s["engine"].ai_choices[-1] == AIChoice(level, "local-vision", "commercial-judge")
    assert _send(s, "general")["job"]["ai_kind"] is None
    assert _post(s, "forms").json()["detail"]["error"] == "unknown_kind"
    # The reports name the kind, never the model
    rows = s["c"]["super"].get(f"{API}/reports/documents", params={"cols": "filename,ai_kind"}).json()["rows"]
    assert {r["ai_kind"] for r in rows} == {"Math and science", "General"}


def test_an_unavailable_kind_falls_back_to_general(setup, monkeypatch):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    _kind(s, level, "Forms", "forms-model")
    _fresh_models(monkeypatch, ["commercial"])  # the gateway doesn't serve forms-model now
    job_id = _send(s, "forms")["job"]["id"]
    run_worker(db)
    assert s["engine"].ai_choices[-1] == AIChoice(level, "commercial")
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["ai_kind_fallback"] == "unavailable"
    # Switched off by a super admin: the same
    s["c"]["super"].patch(f"{API}/admin/services/{level}/kinds/forms", json={"enabled": False})
    assert _post(s, "forms").json()["detail"]["error"] == "unknown_kind"  # no longer offered


def test_a_failing_kind_is_tried_again_with_general(setup, monkeypatch):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    _kind(s, level, "Forms", "forms-model")
    _fresh_models(monkeypatch, None)  # can't tell: assume it's there
    job_id = _send(s, "forms")["job"]["id"]
    run_worker(db)
    assert s["engine"].ai_choices[-1] == AIChoice(level, "forms-model")
    job = db.get(Job, uuid.UUID(job_id))
    s["engine"].fail(job.engine_job_id, "ai_provider_unavailable")
    run_worker(db)  # back in the queue with General…
    run_worker(db)  # …and sent again
    db.refresh(job)
    assert job.ai_kind_fallback == "failed" and s["engine"].ai_choices[-1] == AIChoice(level, "commercial")
    assert job.status in ("submitted", "processing") and job.pages_charged == 2
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "job.ai_fallback")).details["kind"] == "forms"
    # A second failure is a real failure
    s["engine"].fail(job.engine_job_id, "ai_provider_unavailable")
    run_worker(db)
    db.refresh(job)
    assert job.status == "failed"


def test_removing_a_kind(setup):
    s = setup
    level = _tier(s, "Premium AI", "commercial")
    _kind(s, level, "Forms", "forms-model")
    r = s["c"]["super"].delete(f"{API}/admin/services/{level}/kinds/forms")
    assert r.status_code == 200 and r.json()["kinds"] == []
    actions = [e.action for e in s["db"].scalars(select(AuditEvent).where(AuditEvent.action.like("service.kind%")))]
    assert sorted(actions) == ["service.kind_added", "service.kind_removed"]
