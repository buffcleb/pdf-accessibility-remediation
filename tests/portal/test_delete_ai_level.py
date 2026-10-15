"""Deleting an AI level: only one that is switched off with nothing waiting on it; its settings go, its history
stays. There was no way to remove a level made by mistake or no longer offered."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal.models import AiKind, AuditEvent, GroupService, Job, QuotaLedger, Service, UserService
from tests.portal.test_ai_tiers import _tier, _upload
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401


def _off(s, key):
    assert s["c"]["super"].patch(f"{API}/admin/services/{key}", json={"enabled": False}).status_code == 200


def _finish(s, job_id):
    db = s["db"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    db.expire_all()
    assert db.get(Job, uuid.UUID(job_id)).status == "completed"


def test_a_level_switched_off_and_idle_is_deleted_with_its_settings_and_its_history_stays(setup):
    s = setup
    sup, db = s["c"]["super"], s["db"]
    topup(s, s["a"].id, 50)
    key = _tier(s, "Large AI", "large-vision", pages=10, judge="cloud-judge")
    kind = sup.post(f"{API}/admin/services/{key}/kinds", json={"label": "Forms", "gateway_model": "local-vision"})
    assert kind.status_code in (200, 201), kind.text
    job_id = _upload(s["c"]["user"], ("a.pdf", pdf_bytes(3)), ai_tier=key).json()[0]["job"]["id"]
    _finish(s, job_id)
    _off(s, key)

    assert s["c"]["admin_a"].delete(f"{API}/admin/services/{key}").status_code == 403
    assert sup.delete(f"{API}/admin/services/{key}").status_code == 204
    db.expire_all()
    assert db.get(Service, key) is None
    assert key not in [x["key"] for x in sup.get(f"{API}/admin/services").json()]
    for model in (AiKind, GroupService, UserService):
        assert db.scalars(select(model).where(model.service_key == key)).all() == []
    # What was done with it is still on record
    assert db.get(Job, uuid.UUID(job_id)).ai_tier == key
    assert db.scalars(select(QuotaLedger).where(QuotaLedger.service_key == key)).all()
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "service.deleted"))
    assert event.details["service"] == key and event.details["label"] == "Large AI"
    assert event.details["gateway_model"] == "large-vision" and event.details["used_pages"] == 3
    # The document still opens, named by the level's key now that its name is gone; people aren't offered it
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["ai_tier"] == key
    assert key not in [c["key"] for c in s["c"]["user"].get(f"{API}/services").json()]
    assert _upload(s["c"]["user"], ("b.pdf", pdf_bytes(1)), ai_tier=key).status_code in (403, 404, 422)
    # Gone is gone, and the name is free again
    assert sup.delete(f"{API}/admin/services/{key}").status_code == 404
    assert sup.post(f"{API}/admin/services", json={"label": "Large AI", "gateway_model": "large-vision"}).status_code == 201


def test_a_level_that_is_on_or_has_a_document_in_progress_is_not_deleted(setup):
    s = setup
    sup, db = s["c"]["super"], s["db"]
    topup(s, s["a"].id, 50)
    key = _tier(s, "Large AI", "large-vision", pages=10)
    r = sup.delete(f"{API}/admin/services/{key}")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "service_on"

    job_id = _upload(s["c"]["user"], ("a.pdf", pdf_bytes(2)), ai_tier=key).json()[0]["job"]["id"]
    _off(s, key)   # switched off with a document already waiting
    r = sup.delete(f"{API}/admin/services/{key}")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "service_in_use"
    run_worker(db)   # sent to the engine: still in progress
    assert sup.delete(f"{API}/admin/services/{key}").status_code == 409
    assert db.get(Service, key) is not None
    _finish(s, job_id)
    assert sup.delete(f"{API}/admin/services/{key}").status_code == 204


def test_processing_services_are_never_deleted(setup):
    sup = setup["c"]["super"]
    r = sup.delete(f"{API}/admin/services/standard")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "not_ai_tier"
    assert "standard" in [x["key"] for x in sup.get(f"{API}/admin/services").json()]
    assert sup.delete(f"{API}/admin/services/ai:nothing").status_code == 404


def test_pages_given_back_for_a_document_skip_a_level_deleted_since(setup):
    from tests.portal.test_help import _ask

    s = setup
    sup = s["c"]["super"]
    topup(s, s["a"].id, 50)
    key = _tier(s, "Large AI", "large-vision", pages=10)
    job_id = _upload(s["c"]["user"], ("a.pdf", pdf_bytes(2)), ai_tier=key).json()[0]["job"]["id"]
    _finish(s, job_id)
    req = _ask(s, job_id).json()
    _off(s, key)
    assert sup.delete(f"{API}/admin/services/{key}").status_code == 204
    used = s["c"]["user"].get(f"{API}/me").json()["used_pages"]
    r = sup.post(f"{API}/help/{req['id']}/resolve", json={"note": "Fixed the table.", "refund_pages": 2})
    assert r.status_code == 200, r.text
    assert r.json()["pages_refunded"] == 2
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] == used - 2   # the processing pages came back
