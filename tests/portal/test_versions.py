"""Tag manager versions: engine outputs become versions; tag edits make new reviewed versions."""
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
    # Every other edit names a tag; the document's title and language don't (#363)
    no_tag = [{"op": "set_alt"}]
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": no_tag}).status_code == 422
    doc = [{"op": "set_doc_title", "title": "Annual report"}, {"op": "set_doc_lang", "lang": "fr-CA"}]
    assert u.post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": doc}).status_code == 201
    for _ in range(2):
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


# --- unsaved edits kept between sessions (#145) ------------------------------------------------------------------

def _draft(s, job_id, client="user"):
    return s["c"][client].get(f"{API}/jobs/{job_id}/tag-draft").json()


def test_unsaved_edits_are_kept_and_come_back(setup):
    s = setup
    job_id = _completed_job(s)
    assert _draft(s, job_id) == {"mine": None, "others": []}
    more = EDITS + [{"op": "set_alt", "element": 5, "expect_role": "Figure", "alt": "A chart of sales by month"}]
    r = s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": EDITS})
    assert r.status_code == 200 and r.json()["based_on"] == 1
    assert s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": more}).status_code == 200
    mine = _draft(s, job_id)["mine"]                      # one draft per person: the second replaced the first
    assert mine["edits"] == more and mine["user_email"] == "user@a.test"
    # Saving a version from it uses it up
    _save(s, job_id)
    assert _draft(s, job_id)["mine"] is None


def test_no_edits_removes_the_draft_and_bad_ones_are_refused(setup):
    s = setup
    job_id = _completed_job(s)
    put = lambda body: s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json=body)  # noqa: E731
    assert put({"based_on": 1, "edits": EDITS}).status_code == 200
    assert put({"based_on": 1, "edits": []}).json() is None and _draft(s, job_id)["mine"] is None
    assert put({"based_on": 99, "edits": EDITS}).status_code == 404            # no such version
    assert put({"based_on": 1, "edits": [{"op": "explode", "element": 1}]}).status_code == 422
    assert put({"based_on": 1, "edits": EDITS}).status_code == 200
    assert s["c"]["user"].delete(f"{API}/jobs/{job_id}/tag-draft").status_code == 204
    assert _draft(s, job_id)["mine"] is None


def test_list_edits_are_kept_and_sent_to_the_engine(setup):
    """Making one list of several tags, changing an item's level, building a list, splitting a figure, making a
    list a table of contents and naming a cell's headers are edits the portal passes on."""
    s = setup
    job_id = _completed_job(s)
    edits = [{"op": "make_list", "element": 3, "expect_role": "P", "absorb": [4, 5]},
             {"op": "indent", "element": 4, "expect_role": "P"}, {"op": "outdent", "element": 4, "expect_role": "P"},
             {"op": "build_list", "element": 7, "expect_role": "P", "absorb": [8],
              "rows": [{"at": 0, "as": "H3"}, {"at": 2, "level": 1}, {"el": 8, "at": 0, "level": 2}]},
             {"op": "take_pieces", "element": 9, "expect_role": "Figure", "pieces": [{"page": 1, "mcid": 4}], "role": "Figure", "alt": "One panel"},
             {"op": "cut_area", "element": 9, "expect_role": "Figure", "page": 1, "bbox": [300, 200, 420, 290], "role": "Figure", "alt": "One structure"},
             {"op": "make_toc", "element": 12, "expect_role": "L"},
             {"op": "set_headers", "element": 20, "expect_role": "TD", "headers": [15, 17]},
             {"op": "link_note", "element": 22, "expect_role": "P", "note": 30}]
    assert s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": edits}).status_code == 200
    assert _draft(s, job_id)["mine"]["edits"] == edits
    assert s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": edits}).status_code == 201


def test_each_person_has_their_own_and_can_start_from_anothers(setup):
    s = setup
    job_id = _completed_job(s)
    s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": EDITS})
    # The group admin, who can edit too, sees that the owner has unsaved changes (who, when, how many; not what)
    seen = _draft(s, job_id, "admin_a")
    assert seen["mine"] is None and len(seen["others"]) == 1
    other = seen["others"][0]
    assert other["user_email"] == "user@a.test" and other["edits_count"] == 1 and "edits" not in other
    # ...and can start their own from them; the owner's stay as they are
    copied = s["c"]["admin_a"].get(f"{API}/jobs/{job_id}/tag-draft/{other['user_id']}")
    assert copied.status_code == 200 and copied.json()["edits"] == EDITS
    s["c"]["admin_a"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": copied.json()["edits"] + EDITS})
    assert len(_draft(s, job_id)["mine"]["edits"]) == 1 and len(_draft(s, job_id, "admin_a")["mine"]["edits"]) == 2
    assert len(_draft(s, job_id)["others"]) == 1


def test_only_people_who_can_edit_have_drafts(setup):
    s = setup
    job_id = _completed_job(s)
    s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": EDITS})
    owner = _draft(s, job_id)["mine"]["user_id"]
    # A super admin who isn't this group's admin can view the document, but sees and keeps no drafts
    assert _draft(s, job_id, "super") == {"mine": None, "others": []}
    assert s["c"]["super"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": EDITS}).status_code == 403
    assert s["c"]["super"].get(f"{API}/jobs/{job_id}/tag-draft/{owner}").status_code == 403
    # Someone who can't see the document at all
    assert s["c"]["user2"].get(f"{API}/jobs/{job_id}/tag-draft").status_code == 404
    assert s["c"]["admin_b"].get(f"{API}/jobs/{job_id}/tag-draft/{owner}").status_code == 404


# --- saving into the same reviewed version (#145) ----------------------------------------------------------------

def _finish(s, number, ok=True):
    db = s["db"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == number))
    if ok:
        s["engine"].complete_edit(ver.engine_job_id)
    else:
        s["engine"].fail(ver.engine_job_id)
    worker.poll_versions(db)
    db.expire_all()


def _listed(s, job_id):
    return s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"]


def test_saving_into_a_reviewed_version_replaces_it_and_keeps_its_name(setup):
    s = setup
    job_id = _completed_job(s)
    first = _save(s, job_id)
    _finish(s, first["number"])
    was = _listed(s, job_id)[-1]
    again = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions",
                                json={"based_on": first["number"], "edits": EDITS, "replace": True})
    assert again.status_code == 201, again.text
    new = again.json()
    assert new["label"] == "Human reviewed 1" and new["replacing"] == first["number"] and new["status"] == "queued"
    assert [v["label"] for v in _listed(s, job_id) if v["kind"] == "reviewed"] == ["Human reviewed 1"] * 2  # while saving
    _finish(s, new["number"])
    now = [v for v in _listed(s, job_id) if v["kind"] == "reviewed"]
    assert len(now) == 1 and now[0]["number"] == new["number"] and now[0]["label"] == "Human reviewed 1"
    assert now[0]["pdf_name"] == was["pdf_name"] and now[0]["pdf_file_id"] != was["pdf_file_id"]
    assert now[0]["undo_until"] is not None and now[0]["replacing"] is None
    # The one before is out of sight, and the next new version doesn't reuse its name
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{first['number']}/pdf").status_code == 404
    assert _save(s, job_id)["label"] == "Human reviewed 2"


def test_only_a_reviewed_version_can_be_saved_into(setup):
    s = setup
    job_id = _completed_job(s)
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS, "replace": True})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "not_replaceable"


def test_a_save_into_can_be_undone_for_a_day(setup):
    from datetime import timedelta

    s, db = setup, setup["db"]
    job_id = _completed_job(s)
    first = _save(s, job_id)
    _finish(s, first["number"])
    was = _listed(s, job_id)[-1]
    new = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions",
                              json={"based_on": first["number"], "edits": EDITS, "replace": True}).json()
    _finish(s, new["number"])
    assert s["c"]["super"].post(f"{API}/jobs/{job_id}/versions/{new['number']}/undo-save").status_code == 403
    undone = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions/{new['number']}/undo-save")
    assert undone.status_code == 200, undone.text
    back = [v for v in _listed(s, job_id) if v["kind"] == "reviewed"]
    assert len(back) == 1 and back[0]["number"] == first["number"] and back[0]["pdf_file_id"] == was["pdf_file_id"]
    assert back[0]["undo_until"] is None
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{first['number']}/pdf").status_code == 200
    assert s["c"]["user"].post(f"{API}/jobs/{job_id}/versions/{first['number']}/undo-save").status_code == 409

    # After a day the one before is removed for good, and there is nothing to undo
    new = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions",
                              json={"based_on": first["number"], "edits": EDITS, "replace": True}).json()
    _finish(s, new["number"])
    old = db.scalar(select(DocumentVersion).where(DocumentVersion.number == first["number"]))
    old.superseded_at -= timedelta(hours=25)
    db.commit()
    assert worker.purge_superseded(db) == 1
    db.expire_all()
    assert db.scalar(select(DocumentVersion).where(DocumentVersion.number == first["number"])) is None
    assert _listed(s, job_id)[-1]["undo_until"] is None
    assert s["c"]["user"].post(f"{API}/jobs/{job_id}/versions/{new['number']}/undo-save").status_code == 409


def test_a_failed_save_into_leaves_the_version_as_it_was(setup):
    s = setup
    job_id = _completed_job(s)
    first = _save(s, job_id)
    _finish(s, first["number"])
    new = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions",
                              json={"based_on": first["number"], "edits": EDITS, "replace": True}).json()
    _finish(s, new["number"], ok=False)
    vs = {v["number"]: v for v in _listed(s, job_id)}
    assert vs[first["number"]]["status"] == "ready" and vs[first["number"]]["label"] == "Human reviewed 1"
    assert vs[new["number"]]["status"] == "failed" and vs[new["number"]]["label"] == "Human reviewed 1 (not saved)"
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{first['number']}/pdf").status_code == 200


# --- "Describe with AI": alt text for an area, allowed by service default, group and person ---------------------

def _me(client):
    return client.get(f"{API}/me").json()


def _with_ai_level(s, job_id):
    """The document as processed with an AI level that has a gateway model."""
    from tests.portal.test_ai_tiers import _tier
    key = _tier(s, "Vision AI", "local-vision")
    job = s["db"].get(Job, uuid.UUID(job_id))
    job.ai_tier = key
    s["db"].commit()


def test_describe_follows_service_default_then_group_then_person(setup):
    s = setup
    user = s["c"]["user"]
    uid = _me(user)["id"]
    assert _me(user)["can_describe"] is False                                  # off by default
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"ai_describe_default": True}).status_code == 200
    assert _me(user)["can_describe"] is True                                   # the service default
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"ai_describe": False})
    assert r.status_code == 200 and r.json()["ai_describe"] is False
    assert _me(user)["can_describe"] is False                                  # the group overrides it
    r = s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/users/{uid}", json={"ai_describe": True})
    assert r.status_code == 200 and r.json()["ai_describe"] is True
    assert _me(user)["can_describe"] is True                                   # and the person the group
    assert _me(s["c"]["user2"])["can_describe"] is False                       # others in the group follow the group
    # null goes back to following the level above
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/users/{uid}", json={"ai_describe": None})
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"ai_describe": None})
    assert _me(user)["can_describe"] is True


def test_only_admins_set_describe_for_a_group_and_its_people(setup):
    s = setup
    uid = _me(s["c"]["user"])["id"]
    assert s["c"]["user"].patch(f"{API}/groups/{s['a'].id}", json={"ai_describe": True}).status_code in (403, 404)
    assert s["c"]["admin_b"].patch(f"{API}/groups/{s['a'].id}", json={"ai_describe": True}).status_code in (403, 404)
    assert s["c"]["admin_b"].patch(f"{API}/groups/{s['a'].id}/users/{uid}", json={"ai_describe": True}).status_code in (403, 404)
    assert s["c"]["admin_a"].patch(f"{API}/admin/settings", json={"ai_describe_default": True}).status_code == 403
    assert s["c"]["super"].patch(f"{API}/groups/{s['a'].id}/users/{uid}", json={"ai_describe": True}).status_code == 200


def test_describe_an_area_writes_alt_text_and_records_its_cost(setup):
    s = setup
    job_id = _completed_job(s)
    _with_ai_level(s, job_id)
    body = {"page": 1, "bbox": [100, 100, 300, 260], "long": True}
    url = f"{API}/jobs/{job_id}/versions/1/describe"
    r = s["c"]["user"].post(url, json=body)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "ai_describe_off"      # not turned on yet
    uid = _me(s["c"]["user"])["id"]
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}/users/{uid}", json={"ai_describe": True})
    r = s["c"]["user"].post(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["alt"] == "Two rings joined by a bond"
    assert "cost_usd" not in r.json()        # dollars are super admins' (#314): the job's cost keeps it for them
    name, page, bbox, model, long, size = s["engine"].described[-1]
    assert (page, bbox, model, long) == (1, [100, 100, 300, 260], "local-vision", True) and size > 0
    s["c"]["user"].post(url, json=body)
    s["db"].expire_all()
    cost = s["db"].get(Job, uuid.UUID(job_id)).cost
    assert cost["describe_calls"] == 2 and abs(cost["describe_usd"] - 0.0024) < 1e-9
    # ...each use in the log the Describe with AI report reads (#318)
    assert [(u["user"], u["usd"], u["page"]) for u in cost["describe_log"]] == [(uid, 0.0012, 1)] * 2
    # ...and the audit log, which group admins read, doesn't carry it
    from portal.models import AuditEvent
    events = [e for e in s["db"].scalars(select(AuditEvent)) if e.action == "version.ai_described"]
    assert events and not any("cost_usd" in (e.details or {}) for e in events)
    # someone else in the group can't, even when allowed, and nor can anyone without a box
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"ai_describe": True})
    assert s["c"]["user2"].post(url, json=body).status_code in (403, 404)
    assert s["c"]["user"].post(url, json={"page": 1, "bbox": [10, 10, 10, 50]}).status_code == 422


def test_describe_needs_an_ai_level_and_says_when_the_ai_is_down(setup):
    from portal.engine import EngineUnavailable
    s = setup
    job_id = _completed_job(s)
    s["c"]["super"].patch(f"{API}/admin/settings", json={"ai_describe_default": True})
    url = f"{API}/jobs/{job_id}/versions/1/describe"
    body = {"page": 1, "bbox": [100, 100, 300, 260]}
    s["db"].get(Job, uuid.UUID(job_id)).ai_tier = None
    s["db"].commit()
    r = s["c"]["user"].post(url, json=body)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "no_ai_level"
    _with_ai_level(s, job_id)
    s["engine"].describe_answer = EngineUnavailable("gateway down")
    r = s["c"]["user"].post(url, json=body)
    assert r.status_code == 503 and r.json()["detail"]["error"] == "ai_unavailable"
