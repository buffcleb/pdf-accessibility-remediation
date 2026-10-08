"""Tag viewer versions: engine outputs become versions; tag edits make new reviewed versions."""
from __future__ import annotations

import uuid

from portal.models import DocumentVersion, Job
from tests.portal.test_jobs import (  # noqa: F401  (fixtures)
    API, fake_engine, pdf_bytes, run_worker, setup, topup, upload,
)
from portal import worker
from sqlalchemy import select


def _completed_job(s, two_files=False):
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("doc.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id, two_files=two_files)
    run_worker(s["db"])
    return job_id


EDITS = [{"op": "set_role", "element": 3, "expect_role": "P", "role": "H2"}]


def test_outputs_become_versions_with_tag_maps(setup):
    s = setup
    job_id = _completed_job(s, two_files=True)
    body = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()
    assert body["can_edit"] is True
    assert [(v["number"], v["kind"], v["label"]) for v in body["versions"]] == [
        (0, "original", "Original"), (1, "primary", "Remediated"), (2, "visual_fallback", "Visually exact")]
    assert all(v["has_tagmap"] and v["status"] == "ready" for v in body["versions"])
    primary = body["versions"][1]
    assert primary["summary"]["compliant"] is True and primary["summary"]["findings"][0]["severity"] == "review"
    r = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/1/tagmap")
    assert r.status_code == 200 and r.json()["schema"] == 1
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/0/pdf").content.startswith(b"%PDF")
    # Viewer-only files stay out of the generic file list
    kinds = {f["kind"] for f in s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["files"]}
    assert not any(k.startswith("tagmap_") for k in kinds)


def test_save_edits_creates_reviewed_version_and_keeps_original_after(setup):
    s = setup
    job_id = _completed_job(s)
    before_pages = s["c"]["user"].get(f"{API}/me").json()["used_pages"]
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS, "note": "fixed headings"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["label"] == "Human reviewed 1" and v["status"] == "queued" and v["based_on"] == 1
    db = s["db"]
    worker.submit_versions(db)
    sent = [x for x in s["engine"].submitted if x[1] == "edit"]
    assert sent and sent[0][2] == EDITS
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == v["number"]))
    s["engine"].complete_edit(ver.engine_job_id)
    worker.poll_versions(db)
    vs = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"]
    reviewed = vs[-1]
    assert reviewed["status"] == "ready" and reviewed["pdf_name"] == "doc_human_reviewed_2.pdf"
    assert reviewed["summary"]["edits_applied"] == ["element 3: P -> H2"]
    assert reviewed["created_by_email"] == "user@a.test" and reviewed["note"] == "fixed headings"
    # The engine's remediated version is untouched and still downloadable
    assert vs[1]["status"] == "ready" and vs[1]["pdf_file_id"] != reviewed["pdf_file_id"]
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{reviewed['number']}/pdf").status_code == 200
    # Editing uses no pages
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] == before_pages
    # A reviewed version can be the base of the next one
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": reviewed["number"], "edits": EDITS})
    assert r.status_code == 201 and r.json()["label"] == "Human reviewed 2"


def test_failed_edit_is_recorded_with_readable_reason(setup):
    s = setup
    job_id = _completed_job(s)
    s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS})
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.kind == "reviewed"))
    s["engine"].jobs[ver.engine_job_id] = {"status": "failed", "error": {
        "error": "edit_rejected", "detail": "Edit 1: element 3 is a Figure, not a P."}}
    worker.poll_versions(db)
    v = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"][-1]
    assert v["status"] == "failed" and v["error_message"] == "Edit 1: element 3 is a Figure, not a P."


def test_who_can_edit(setup, db):
    s = setup
    job_id = _completed_job(s)
    body = {"based_on": 1, "edits": EDITS}
    # The group admin can (and it's recorded as an admin edit)
    assert s["c"]["admin_a"].get(f"{API}/jobs/{job_id}/versions").json()["can_edit"] is True
    assert s["c"]["admin_a"].post(f"{API}/jobs/{job_id}/versions", json=body).status_code == 201
    ev = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/audit", params={"action": "version.created"}).json()
    assert ev[0]["details"]["by_admin"] is True
    # Another user in the group and another group's admin can't see it at all
    assert s["c"]["user2"].post(f"{API}/jobs/{job_id}/versions", json=body).status_code == 404
    assert s["c"]["admin_b"].get(f"{API}/jobs/{job_id}/versions").status_code == 404
    # A super admin who isn't this group's admin can view but not edit
    assert s["c"]["super"].get(f"{API}/jobs/{job_id}/versions").json()["can_edit"] is False
    assert s["c"]["super"].post(f"{API}/jobs/{job_id}/versions", json=body).status_code == 403


def test_edit_requests_are_checked(setup):
    s = setup
    job_id = _completed_job(s)
    u = s["c"]["user"]
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 0, "edits": EDITS}).status_code == 422
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 9, "edits": EDITS}).status_code == 404
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": []}).status_code == 422
    bad = [{"op": "rm -rf", "element": 1}]
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": bad}).status_code == 422
    for _ in range(3):
        assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS}).status_code == 201
    r = u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS})
    assert r.status_code == 429


def test_admin_viewing_tags_is_recorded(setup):
    s = setup
    job_id = _completed_job(s)
    assert s["c"]["admin_a"].get(f"{API}/jobs/{job_id}/versions/1/tagmap").status_code == 200
    ev = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/audit", params={"action": "file.accessed"}).json()
    assert ev and ev[0]["details"]["kind"] == "tagmap_primary"


def _save(s, job_id, **extra):
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_save_with_ai_review_runs_it_with_the_edits(setup):
    s = setup
    job_id = _completed_job(s)
    v = _save(s, job_id, review="sampled")
    assert v["review"] == "sampled" and v["review_status"] == "queued"
    db = s["db"]
    worker.submit_versions(db)
    sent = [x for x in s["engine"].submitted if x[1] == "edit"]
    assert sent[-1][3] == "sampled"
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == v["number"]))
    assert ver.review_status == "running"
    s["engine"].complete_edit(ver.engine_job_id, findings=[{"severity": "review", "issue": "Alt text looks generic"}])
    worker.poll_versions(db)
    got = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"][-1]
    assert got["status"] == "ready" and got["review_status"] == "done" and got["reviewed_at"]
    assert got["summary"]["findings_total"] == 1 and got["summary"]["ai_review"]["kind"] == "sampled"
    # Without the option nothing extra is asked of the engine
    _save(s, job_id)
    worker.submit_versions(db)
    assert [x for x in s["engine"].submitted if x[1] == "edit"][-1][3] == "none"


def test_review_a_saved_version_later_replaces_its_findings_only(setup):
    s = setup
    job_id = _completed_job(s)
    v = _save(s, job_id)
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == v["number"]))
    s["engine"].complete_edit(ver.engine_job_id)
    worker.poll_versions(db)
    pdf_before = ver.pdf_file_id

    c = s["c"]["user"]
    assert c.post(f"{API}/jobs/{job_id}/versions/1/review").status_code == 422  # engine output: not here
    r = c.post(f"{API}/jobs/{job_id}/versions/{v['number']}/review")
    assert r.status_code == 202 and r.json()["review_status"] == "queued"
    assert c.post(f"{API}/jobs/{job_id}/versions/{v['number']}/review").status_code == 409  # already running
    worker.submit_reviews(db)
    sent = [x for x in s["engine"].submitted if x[1] == "edit"][-1]
    assert sent[2] == [] and sent[3] == "sampled"
    db.refresh(ver)
    assert ver.review_status == "running"
    s["engine"].complete_edit(ver.review_engine_job_id, findings=[
        {"severity": "high", "issue": "Figure alt text doesn't match the image"},
        {"severity": "suggest", "issue": "Heading could be shorter"}])
    worker.poll_reviews(db)
    got = c.get(f"{API}/jobs/{job_id}/versions").json()["versions"][-1]
    assert got["review_status"] == "done" and got["summary"]["findings_total"] == 2
    assert got["summary"]["findings"][0]["severity"] == "high"
    assert got["summary"]["edits_applied"] == ["element 3: P -> H2"]  # kept from the save
    assert got["pdf_file_id"] == str(pdf_before) and got["label"] == v["label"]  # same file, same version


def test_failed_review_keeps_earlier_findings(setup):
    s = setup
    job_id = _completed_job(s)
    v = _save(s, job_id)
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == v["number"]))
    s["engine"].complete_edit(ver.engine_job_id, findings=[{"severity": "review", "issue": "x"}])
    worker.poll_versions(db)
    s["c"]["user"].post(f"{API}/jobs/{job_id}/versions/{v['number']}/review")
    worker.submit_reviews(db)
    db.refresh(ver)
    s["engine"].jobs[ver.review_engine_job_id] = {"status": "failed", "error": {"error": "ai_unavailable"}}
    worker.poll_reviews(db)
    got = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"][-1]
    assert got["review_status"] == "failed" and "unchanged" in got["review_error"]
    assert got["summary"]["findings_total"] == 1


def test_only_editors_can_request_a_review(setup):
    s = setup
    job_id = _completed_job(s)
    v = _save(s, job_id)
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == v["number"]))
    s["engine"].complete_edit(ver.engine_job_id)
    worker.poll_versions(db)
    url = f"{API}/jobs/{job_id}/versions/{v['number']}/review"
    assert s["c"]["user2"].post(url).status_code == 404
    assert s["c"]["admin_b"].post(url).status_code == 404
    assert s["c"]["super"].post(url).status_code == 403
    assert s["c"]["admin_a"].post(url).status_code == 202
    ev = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/audit", params={"action": "version.review_requested"}).json()
    assert ev[0]["details"]["by_admin"] is True
