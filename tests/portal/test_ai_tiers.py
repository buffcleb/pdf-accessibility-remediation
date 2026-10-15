"""AI tiers: gateway models published as services, chosen per upload, charged and costed."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from portal.engine import AIChoice
from portal.models import Job, QuotaLedger
from tests.portal.test_jobs import (  # noqa: F401  (fixtures)
    API, fake_engine, pdf_bytes, run_worker, setup, topup, upload,
)


def _tier(s, label, model, *, metered=True, external=False, default=False, pages=0, judge=None):
    sup, a = s["c"]["super"], s["a"].id
    r = sup.post(f"{API}/admin/services", json={"label": label, "gateway_model": model, "metered": metered,
                                                "external": external, "gateway_judge_model": judge})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    assert r.json()["kind"] == "ai_tier" and not r.json()["enabled"]  # off until switched on
    sup.patch(f"{API}/admin/services/{key}", json={"enabled": True})
    body = {"enabled": True, "all_users": True, **({"is_default": True} if default else {})}
    assert s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/{key}", json=body).status_code == 200
    if pages:
        topup(s, a, pages, service=key)
    return key


def _upload(client, *docs, ai_tier=None):
    files = [("files", (n, d, "application/pdf")) for n, d in docs]
    data = {"ai_tier": ai_tier} if ai_tier else {}
    return client.post(f"{API}/jobs", files=files, data=data)


def test_super_admin_publishes_gateway_models_as_tiers(setup):
    s = setup
    assert s["c"]["super"].get(f"{API}/admin/ai-models").json()["models"] == ["local-vision", "commercial"]
    assert s["c"]["admin_a"].get(f"{API}/admin/ai-models").status_code == 403
    key = _tier(s, "Commercial AI", "commercial", external=True)
    assert key == "ai:commercial-ai"
    r = s["c"]["super"].post(f"{API}/admin/services", json={"label": "Commercial AI", "gateway_model": "x"})
    assert r.status_code == 409
    # Standard isn't an AI tier, so it has no gateway model
    r = s["c"]["super"].patch(f"{API}/admin/services/standard", json={"gateway_model": "local-vision"})
    assert r.status_code == 422


def test_default_tier_is_used_charged_and_sent_to_the_engine(setup):
    s = setup
    topup(s, s["a"].id, 50)
    local = _tier(s, "Local AI", "local-vision", metered=False, default=True)
    user, db = s["c"]["user"], s["db"]
    r = upload(user, ("doc.pdf", pdf_bytes(3))).json()[0]
    assert r["accepted"] and r["job"]["ai_tier"] == local and sorted(r["job"]["charged_services"]) == sorted(["standard", local])
    run_worker(db)
    assert s["engine"].ai_choices[-1] == AIChoice(local, "local-vision")
    # The unmetered tier counts use but never limits it
    me = user.get(f"{API}/me").json()
    lt = next(x for x in me["services"] if x["key"] == local)
    assert lt["reserved_pages"] == 3 and lt["remaining_pages"] is None
    assert me["reserved_pages"] == 3  # standard pages too


def test_choosing_a_metered_tier_needs_its_pages_and_records_cost(setup):
    s = setup
    topup(s, s["a"].id, 50)
    _tier(s, "Local AI", "local-vision", metered=False, default=True)
    big = _tier(s, "Large AI", "large-vision", pages=4)
    user, db = s["c"]["user"], s["db"]
    r = _upload(user, ("a.pdf", pdf_bytes(3)), ("b.pdf", pdf_bytes(3)), ai_tier=big).json()
    assert r[0]["accepted"] and r[1]["error"]["error"] == "over_quota" and r[1]["error"]["service"] == big
    run_worker(db)
    job = db.scalar(select(Job).where(Job.id == uuid.UUID(r[0]["job"]["id"])))
    assert s["engine"].ai_choices[-1] == AIChoice(big, "large-vision")
    s["engine"].complete(job.engine_job_id)
    s["engine"].jobs[job.engine_job_id]["metrics"] = {"ai_cost_usd": 0.0412, "verification_ai_cost_usd": 0.0088,
                                                      "ai_calls": 7, "ai_model": "large-vision"}
    run_worker(db)
    db.refresh(job)
    assert job.status == "completed"
    assert {k: job.cost[k] for k in ("ai_usd", "ai_calls", "ai_tier")} == {"ai_usd": 0.05, "ai_calls": 7, "ai_tier": big}
    # What the AI usage report reads: the writing and review costs apart, and the model that served it
    assert job.cost["writer_usd"] == 0.0412 and job.cost["review_usd"] == 0.0088
    assert job.cost["writer_model"] == "large-vision"
    # Without a default tier and none chosen, the engine's own default AI is used
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/services/ai:local-ai", json={"enabled": False})
    r = upload(user, ("c.pdf", pdf_bytes(1))).json()[0]
    assert r["job"]["ai_tier"] is None


def test_tiers_follow_the_same_opt_in_and_local_only_rules(setup):
    s = setup
    topup(s, s["a"].id, 50)
    commercial = _tier(s, "Commercial AI", "commercial", external=True, pages=100)
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"local_only": True})
    r = _upload(s["c"]["user"], ("a.pdf", pdf_bytes(1)), ai_tier=commercial)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "local_only"
    choices = [c["key"] for c in s["c"]["user"].get(f"{API}/services").json()]
    assert commercial not in choices
    r = _upload(s["c"]["user"], ("a.pdf", pdf_bytes(1)), ai_tier="standard")
    assert r.status_code == 422  # standard isn't an AI tier


def test_gateway_admin_link_is_for_super_admins_only(setup, monkeypatch):
    from portal.config import get_portal_settings

    s = setup
    # No link unless an address is configured (a made-up default would point at the admin's own machine)
    monkeypatch.setattr(get_portal_settings(), "gateway_admin_url", "")
    assert s["c"]["super"].get(f"{API}/admin/links").json()["gateway_admin_url"] is None
    monkeypatch.setattr(get_portal_settings(), "gateway_admin_url", "https://gateway.example.org/ui")
    r = s["c"]["super"].get(f"{API}/admin/links")
    assert r.status_code == 200 and r.json()["gateway_admin_url"].endswith("/ui")
    assert s["c"]["admin_a"].get(f"{API}/admin/links").status_code == 403
    assert s["c"]["user"].get(f"{API}/admin/links").status_code == 403


def test_each_tier_reviews_with_its_own_judge(setup):
    s = setup
    topup(s, s["a"].id, 50)
    cloud = _tier(s, "Cloud AI", "cloud-vision", metered=False, default=True, judge="cloud-judge")
    user, db = s["c"]["user"], s["db"]
    jid = upload(user, ("doc.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(db)
    assert s["engine"].ai_choices[-1] == AIChoice(cloud, "cloud-vision", "cloud-judge")
    s["engine"].complete(db.get(Job, uuid.UUID(jid)).engine_job_id)
    run_worker(db)
    # Saving tag edits (and AI re-reviews) of that document use the same tier's judge
    from portal import worker
    r = user.post(f"{API}/jobs/{jid}/versions", json={"based_on": 1, "review": "sampled",
                                                      "edits": [{"op": "set_role", "element": 3, "role": "H2"}]})
    assert r.status_code == 201, r.text
    worker.submit_versions(db)
    assert s["engine"].judges[-1] == "cloud-judge"
    # The judge can be changed or cleared (back to the engine's default)
    r = s["c"]["super"].patch(f"{API}/admin/services/{cloud}", json={"gateway_judge_model": ""})
    assert r.json()["gateway_judge_model"] is None
    upload(user, ("doc2.pdf", pdf_bytes(1)))
    run_worker(db)
    assert s["engine"].ai_choices[-1] == AIChoice(cloud, "cloud-vision", None)


def test_ai_tier_pages_are_given_back_when_every_ai_call_failed(setup):
    s = setup
    topup(s, s["a"].id, 50)
    big = _tier(s, "Large AI", "large-vision", pages=10)
    user, db = s["c"]["user"], s["db"]
    ids = [x["job"]["id"] for x in _upload(user, ("a.pdf", pdf_bytes(3)), ("b.pdf", pdf_bytes(2)), ai_tier=big).json()]
    run_worker(db)
    jobs = {str(j.id): j for j in db.scalars(select(Job))}
    # a: every AI call failed; b: some worked
    for jid, metrics in ((ids[0], {"ai_calls": 0, "ai_failures": 8}), (ids[1], {"ai_calls": 5, "ai_failures": 2})):
        s["engine"].complete(jobs[jid].engine_job_id)
        s["engine"].jobs[jobs[jid].engine_job_id]["metrics"] = metrics
    run_worker(db)
    me = user.get(f"{API}/me").json()
    tier = next(x for x in me["services"] if x["key"] == big)
    assert tier["used_pages"] == 2 and tier["reserved_pages"] == 0  # only b was charged to the tier
    assert me["used_pages"] == 5  # both were charged their processing pages
    a = user.get(f"{API}/jobs/{ids[0]}").json()
    assert a["ai_refunded"] and a["charged_services"] == ["standard"]
    assert not user.get(f"{API}/jobs/{ids[1]}").json()["ai_refunded"]
    kinds = [(e.service_key, e.kind, e.comment) for e in db.scalars(select(QuotaLedger).where(QuotaLedger.job_id == uuid.UUID(ids[0])))]
    assert (big, "release", "every AI call failed") in kinds and ("standard", "commit", None) in kinds


def test_review_every_figure_on_upload_save_and_re_review(setup):
    s = setup
    topup(s, s["a"].id, 50)
    cloud = _tier(s, "Cloud AI", "cloud-vision", metered=False, default=True, judge="cloud-judge")
    user, db, sup = s["c"]["user"], s["db"], s["c"]["super"]
    assert next(c for c in user.get(f"{API}/services").json() if c["key"] == cloud)["allow_full_review"]
    files = [("files", ("doc.pdf", pdf_bytes(2), "application/pdf"))]
    r = user.post(f"{API}/jobs", files=files, data={"full_review": "true"}).json()[0]
    assert r["job"]["full_review"]
    run_worker(db)
    assert s["engine"].full_reviews[-1] is True
    jid = r["job"]["id"]
    s["engine"].complete(db.get(Job, uuid.UUID(jid)).engine_job_id)
    run_worker(db)
    # Saving with a full review, then asking for another full review later
    from portal import worker
    v = user.post(f"{API}/jobs/{jid}/versions", json={"based_on": 1, "review": "full",
                                                      "edits": [{"op": "set_role", "element": 3, "role": "H2"}]})
    assert v.status_code == 201 and v.json()["review"] == "full"
    worker.submit_versions(db)
    assert s["engine"].submitted[-1][3] == "full"
    ver = v.json()["number"]
    from portal.models import DocumentVersion
    dv = db.scalar(select(DocumentVersion).where(DocumentVersion.number == ver))
    s["engine"].complete_edit(dv.engine_job_id)
    worker.poll_versions(db)
    r = user.post(f"{API}/jobs/{jid}/versions/{ver}/review", json={"full": True})
    assert r.status_code == 202 and r.json()["review"] == "full"
    # Switched off for the tier: refused everywhere, the plain review still works
    sup.patch(f"{API}/admin/services/{cloud}", json={"allow_full_review": False})
    worker.submit_reviews(db)
    db.refresh(dv)
    s["engine"].complete_edit(dv.review_engine_job_id)
    worker.poll_reviews(db)
    assert user.post(f"{API}/jobs/{jid}/versions/{ver}/review", json={"full": True}).json()["detail"]["error"] == \
        "full_review_not_allowed"
    assert user.post(f"{API}/jobs/{jid}/versions/{ver}/review").status_code == 202
    r = user.post(f"{API}/jobs", files=[("files", ("d.pdf", pdf_bytes(1), "application/pdf"))], data={"full_review": "true"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "full_review_not_allowed"


def test_tier_can_switch_off_ai_notation_reading(setup):
    s = setup
    cloud = _tier(s, "Cloud AI", "cloud-vision", metered=False, default=True)
    sup = s["c"]["super"]
    svc = next(x for x in sup.get(f"{API}/admin/services").json() if x["key"] == cloud)
    assert svc["allow_notation"] is True  # on by default
    topup(s, s["a"].id, 50)
    upload(s["c"]["user"], ("a.pdf", pdf_bytes(1)))
    run_worker(s["db"])
    assert s["engine"].ai_choices[-1].notation is True
    assert sup.patch(f"{API}/admin/services/{cloud}", json={"allow_notation": False}).json()["allow_notation"] is False
    upload(s["c"]["user"], ("b.pdf", pdf_bytes(1)))
    run_worker(s["db"])
    assert s["engine"].ai_choices[-1].notation is False
    assert s["engine"].ai_choices[-1].ocr is True
    assert sup.patch(f"{API}/admin/services/{cloud}", json={"allow_ocr_ai": False}).json()["allow_ocr_ai"] is False
    upload(s["c"]["user"], ("c.pdf", pdf_bytes(1)))
    run_worker(s["db"])
    assert s["engine"].ai_choices[-1].ocr is False
