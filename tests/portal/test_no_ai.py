"""No AI as a choice for remediation (#659): offered by super admins, then by each group (beside its AI levels,
preselected, or as the only choice); sent to the engine with no AI at all, charged on the processing service only;
a document with no AI level made the same way; and no AI for the document afterwards in the tag manager."""
from __future__ import annotations

import json
import uuid

import httpx

from portal.engine import HttpEngine
from portal.models import Job
from portal import worker
from portal.models import DocumentVersion
from sqlalchemy import select
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_api_tokens import _script
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401
from tests.portal.test_versions import EDITS, _completed_job


def _post(s, who="user", **data):
    files = [("files", ("d.pdf", pdf_bytes(2), "application/pdf"))]
    return s["c"][who].post(f"{API}/jobs", files=files, data={k: v for k, v in data.items() if v is not None})


def _job(s, **data):
    r = _post(s, **data)
    assert r.status_code == 200 and r.json()[0]["accepted"], r.text
    return r.json()[0]["job"]


def _group(s, **body):
    return s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json=body)


def _offer(s, on=True):
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"no_ai_enabled": on}).status_code == 200


def test_no_ai_is_offered_by_super_admins_then_the_group(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, pages=10)
    refused = _post(s, ai_tier="none")  # refused before any file is taken
    assert refused.status_code == 403 and refused.json()["detail"]["error"] == "no_ai_not_offered"
    # The group turns it on, but it takes effect only once super admins offer it
    r = _group(s, no_ai="on")
    assert r.status_code == 200 and r.json()["no_ai"] == "on" and r.json()["no_ai_offered"] is False
    assert s["c"]["user"].get(f"{API}/upload-options").json()["no_ai"] == "off"
    assert _post(s, ai_tier="none").status_code == 403
    _offer(s)
    assert s["c"]["user"].get(f"{API}/upload-options").json()["no_ai"] == "on"
    assert s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}").json()["no_ai_offered"] is True

    job = _job(s, ai_tier="none", full_review="true")
    assert job["ai_tier"] is None and job["no_ai"] is True and job["ai_label"] == "No AI"
    # Charged on the processing service only: the level's pages are untouched
    assert job["pages_charged"] == 2 and job["charged_services"] == ["standard"] and job["full_review"] is False
    assert next(x for x in s["c"]["user"].get(f"{API}/services").json() if x["key"] == level)["remaining_pages"] == 10
    run_worker(db)
    assert s["engine"].ai_choices[-1] is None and s["engine"].no_ais[-1] is True
    assert s["engine"].brandings[-1]["ai_label"] == "No AI"
    # The level is still the default
    assert _job(s)["ai_tier"] == level
    # Super admins stop offering it: refused again
    _offer(s, False)
    assert _post(s, ai_tier="none").status_code == 403
    assert _group(s, no_ai="sometimes").status_code == 422


def test_a_group_preselects_no_ai_or_makes_it_the_only_choice(setup):
    s = setup
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    _offer(s)
    assert _group(s, no_ai="default").status_code == 200
    assert s["c"]["user"].get(f"{API}/upload-options").json()["no_ai"] == "default"
    assert _job(s)["no_ai"] is True  # ahead of the group's default level
    assert _job(s, ai_tier=level)["ai_tier"] == level  # a level can still be picked

    # The only choice: no kind, and No AI turned on with it
    assert _group(s, no_ai="off").status_code == 200
    r = _group(s, ai_mode="fixed", ai_level="none", ai_kind="math")
    assert r.status_code == 200, r.text
    assert (r.json()["ai_level"], r.json()["ai_kind"], r.json()["no_ai"]) == ("none", None, "on")
    opts = s["c"]["user"].get(f"{API}/upload-options").json()
    assert (opts["ai_mode"], opts["ai_level"]) == ("fixed", "none")
    assert _job(s, ai_tier=level)["no_ai"] is True  # what's asked for is set aside
    assert _group(s, no_ai="off").json()["detail"]["error"] == "no_ai_fixed"
    # Super admins stop offering it: uploads say so, and it can't be set for everything
    _offer(s, False)
    refused = _post(s)
    assert refused.status_code == 403 and refused.json()["detail"]["error"] == "no_ai_not_offered"
    assert _group(s, ai_mode="fixed", ai_level="none").status_code == 403
    _offer(s)
    assert _group(s, ai_mode="choose").status_code == 200
    assert _job(s)["ai_tier"] == level


def test_a_document_with_no_ai_level_is_made_without_ai(setup):
    """Never the engine's fallback AI: the group has no level, so the document goes with no AI at all."""
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 50)
    job = _job(s)
    assert job["ai_tier"] is None and job["no_ai"] is True and job["ai_label"] == "No AI"
    run_worker(db)
    assert s["engine"].ai_choices[-1] is None


def test_the_engine_gets_provider_none_without_a_level(monkeypatch):
    sent = []

    def answer(request):
        sent.append(request.content.decode("latin-1"))
        return httpx.Response(200, json={"job_id": "j1"})

    eng = HttpEngine.__new__(HttpEngine)
    eng.client = httpx.Client(base_url="http://engine", transport=httpx.MockTransport(answer))
    eng.provider, eng.model = "localai", "fallback-model"  # the fallback: never used for a document
    assert eng.submit("d.pdf", b"%PDF", False) == "j1"
    body = sent[-1]
    assert 'name="ai_provider"\r\n\r\nnone' in body and "fallback-model" not in body and "localai" not in body
    flags = json.loads(body.split('name="flags"\r\n\r\n', 1)[1].split("\r\n--", 1)[0])
    assert flags["verify_ai"] is False and flags["ocr_ai"] is False and flags["notation_ai"] is False


def test_a_local_only_group_can_use_no_ai(setup):
    s = setup
    topup(s, s["a"].id, 50)
    _tier(s, "Cloud AI", "commercial", external=True, default=True, metered=False)
    _offer(s)
    assert _group(s, local_only=True, no_ai="on").status_code == 200
    levels = [x for x in s["c"]["user"].get(f"{API}/services").json() if x["kind"] == "ai_tier"]
    assert levels == []  # its only level sends documents out
    job = _job(s, ai_tier="none")
    assert job["no_ai"] is True and job["external_services"] == []


def test_subgroups_take_no_ai_from_their_parent(setup):
    s = setup
    _offer(s)
    r = s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/subgroups", json={"name": "Records"})
    assert r.status_code == 201, r.text
    sub = r.json()["id"]
    assert _group(s, no_ai="default").status_code == 200
    assert s["c"]["admin_a"].get(f"{API}/groups/{sub}").json()["no_ai"] == "default"


def test_an_api_token_keeps_no_ai(setup, make_client):
    s = setup
    topup(s, s["a"].id, 50)
    level = _tier(s, "Premium AI", "commercial", default=True, metered=False)
    _offer(s)
    s["c"]["super"].patch(f"{API}/admin/settings", json={"api_tokens_enabled": True})
    assert _group(s, no_ai="on", api_tokens_enabled=True).status_code == 200
    r = s["c"]["user"].post(f"{API}/me/api-tokens", json={"name": "nightly", "ai_tier": "none"})
    assert r.status_code == 201, r.text
    assert r.json()["info"]["ai_tier"] == "none" and r.json()["info"]["sends_to"].endswith("No AI")
    tok = r.json()["token"]
    files = [("files", ("d.pdf", pdf_bytes(2), "application/pdf"))]
    sent = _script(s, make_client, tok).post(f"{API}/jobs", files=files, data={"ai_tier": level})
    assert sent.status_code == 200, sent.text
    assert sent.json()[0]["job"]["no_ai"] is True  # the token's choice, whatever is sent


def test_a_no_ai_document_is_never_sent_to_ai_afterwards(setup):
    s = setup
    job_id = _completed_job(s)
    assert s["db"].get(Job, uuid.UUID(job_id)).no_ai
    c = s["c"]["user"]
    s["c"]["super"].patch(f"{API}/admin/settings", json={"ai_describe_default": True})
    for url, body in ((f"{API}/jobs/{job_id}/versions/1/describe", {"page": 1, "bbox": [10, 10, 200, 200]}),
                      (f"{API}/jobs/{job_id}/versions", {"based_on": 1, "edits": EDITS, "review": "sampled"})):
        r = c.post(url, json=body)
        assert r.status_code == 409 and r.json()["detail"]["error"] == "no_ai_document", (url, r.text)
        assert "made without AI" in r.json()["detail"]["message"]
    saved = c.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS})
    assert saved.status_code == 201  # a save without AI review goes ahead
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == saved.json()["number"]))
    s["engine"].complete_edit(ver.engine_job_id)
    worker.poll_versions(db)
    r = c.post(f"{API}/jobs/{job_id}/versions/{ver.number}/review")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "no_ai_document"
    assert [x[3] for x in s["engine"].submitted if x[1] == "edit"] == ["none"]


def test_a_group_fixed_to_no_ai_can_change_its_other_settings_when_it_isnt_offered(setup):
    s = setup
    _offer(s)
    assert _group(s, ai_mode="fixed", ai_level="none").status_code == 200
    _offer(s, False)
    assert _group(s, show_external=True).status_code == 200
    assert _group(s, ai_mode="fixed", ai_level="none").status_code == 403
