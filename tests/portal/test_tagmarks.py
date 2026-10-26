"""Which tags were in the document as uploaded (shown in the tag manager when its tags were kept)."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from portal import tagmarks, worker
from portal.models import DocumentVersion, Job, JobFile
from portal.storage import get_storage
from portal.tagmarks import MARKED, mark_all, mark_original
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def _el(i, role, parent, kids=(), **kw):
    return {"i": i, "role": role, "parent": parent, "kids": list(kids), "boxes": [], "own": False, "order": None, **kw}


def _piece(el, mcid, text, page=1, y=100.0, kind="text"):
    return {"page": page, "mcid": mcid, "el": el, "bbox": [72.0, y, 300.0, y + 12], "kind": kind, "text": text}


ORIGINAL = {
    "schema": 1, "pages": [{"w": 612, "h": 792}],
    "elements": [_el(0, "Document", None, [1, 2, 3]), _el(1, "H1", 0), _el(2, "P", 0),
                 _el(3, "Form", 0, annot=True, boxes=[{"page": 1, "bbox": [72.0, 300.0, 200.0, 320.0]}])],
    "pieces": [_piece(1, 0, "Title", y=80), _piece(2, 1, "Body text", y=120)],
}


def _remediated():
    return {
        "schema": 1, "pages": [{"w": 612, "h": 792}],
        "elements": [
            _el(0, "Document", None, [1]), _el(1, "Sect", 0, [2, 3, 4, 5, 6, 7]),
            _el(2, "H1", 1), _el(3, "P", 1),                                    # the original's
            _el(4, "P", 1),                                                     # content its tags left out
            _el(5, "Form", 1, annot=True, boxes=[{"page": 1, "bbox": [72.4, 300.2, 200.0, 320.0]}]),  # its field
            _el(6, "Form", 1, annot=True, boxes=[{"page": 1, "bbox": [72.0, 400.0, 200.0, 420.0]}]),  # an untagged one
            _el(7, "L", 1, [8]), _el(8, "LI", 7),                               # a list made from left-out content
        ],
        "pieces": [_piece(2, 0, "Title", y=80.4), _piece(3, 1, "Body text", y=120), _piece(4, 2, "Left out", y=160),
                   _piece(8, 3, "An item", y=200)],
    }


def test_tags_holding_the_originals_content_are_the_originals():
    tm = mark_original(_remediated(), ORIGINAL)
    marked = {e["i"] for e in tm["elements"] if e.get("orig")}
    # the heading, the paragraph and the field; and the groups they're in. Not what was added, nor its list.
    assert marked == {0, 1, 2, 3, 5}


def test_the_same_number_with_other_content_is_not_the_originals():
    """A page tagged afresh numbers its pieces from 0 again: the number alone proves nothing."""
    tm = _remediated()
    tm["pieces"][0]["text"] = "Another heading"    # same page and number, different words
    tm["pieces"][1]["bbox"] = [72.0, 500.0, 300.0, 512.0]  # same words, another place
    marked = {e["i"] for e in mark_original(tm, ORIGINAL)["elements"] if e.get("orig")}
    assert marked == {0, 1, 5}


def test_nothing_is_marked_without_original_tags_to_compare():
    for original in (None, {"elements": [], "pieces": []}, {"elements": ORIGINAL["elements"]}):  # untagged; pre-pieces
        assert not any(e.get("orig") for e in mark_original(_remediated(), original)["elements"])
    assert all(e["orig"] for e in mark_all(_remediated())["elements"])


def _put_tagmap(db, version, tagmap):
    """A tag map stored as before #580: without its marks."""
    f = db.get(JobFile, version.tagmap_file_id)
    f.size_bytes = get_storage().put_bytes(f.s3_key, json.dumps(tagmap).encode(), "application/json")
    db.commit()


def test_the_viewer_gets_the_marks_only_when_the_documents_tags_were_kept(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("tagged.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    versions = {v.kind: v for v in db.scalars(select(DocumentVersion).where(DocumentVersion.job_id == uuid.UUID(job_id)))}
    _put_tagmap(db, versions["original"], ORIGINAL)
    _put_tagmap(db, versions["primary"], _remediated())
    get = lambda v: s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{v.number}/tagmap").json()  # noqa: E731

    # Not known to have kept its tags (an untagged original, a rebuilt tree, a job from before this): no marks
    assert not any(e.get("orig") for e in get(versions["primary"])["elements"])
    job = db.get(Job, uuid.UUID(job_id))
    job.result_summary = {**job.result_summary, "tags_kept": True}
    db.commit()
    assert {e["i"] for e in get(versions["primary"])["elements"] if e.get("orig")} == {0, 1, 2, 3, 5}
    assert all(e.get("orig") for e in get(versions["original"])["elements"])  # the upload's own tags: all of them


def test_the_result_says_whether_the_documents_tags_were_kept():
    from portal.worker import summarize

    def kept(existing):
        return summarize({"reports": {"comparison": {"existing_tags": existing}}})["tags_kept"]
    assert kept({"present": True, "rebuilt_tree": False}) is True
    assert kept({"present": True, "rebuilt_tree": True}) is False   # unusable tags, tagged afresh
    assert kept({"present": False}) is False and kept(None) is False


# #580: the marks are written when a version's tag map is stored, and the viewer gets the file as it is

def _engine_with_kept_tags(s):
    """The fake engine's tag maps as a tagged upload's: the original's tags, and a remediated map that kept them."""
    fetch = s["engine"].fetch

    def tagmaps(path, out):
        if "tagmap" not in path:
            return fetch(path, out)
        data = json.dumps(ORIGINAL if "tagmap_original" in path else _remediated()).encode()
        out.write(data)
        return len(data)
    s["engine"].fetch = tagmaps


def _kept_tags_job(s):
    _engine_with_kept_tags(s)
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("tagged.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    db = s["db"]
    run_worker(db)
    jid = db.get(Job, uuid.UUID(job_id)).engine_job_id
    s["engine"].complete(jid)
    s["engine"].jobs[jid]["reports"]["comparison"]["existing_tags"] = {"present": True, "rebuilt_tree": False}
    run_worker(db)
    return job_id


def _stored(db, job_id, number):
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == uuid.UUID(job_id), DocumentVersion.number == number))
    f = db.get(JobFile, v.tagmap_file_id)
    data = b"".join(get_storage().get_stream(f.s3_key))
    assert f.size_bytes == len(data)
    return data


def test_tag_maps_are_stored_with_their_marks_and_served_as_they_are(setup, monkeypatch):
    s, db = setup, setup["db"]
    job_id = _kept_tags_job(s)
    assert db.get(Job, uuid.UUID(job_id)).result_summary["tags_kept"] is True
    original, primary = _stored(db, job_id, 0), _stored(db, job_id, 1)
    assert original.startswith(MARKED) and primary.startswith(MARKED)
    assert {e["i"] for e in json.loads(primary)["elements"] if e.get("orig")} == {0, 1, 2, 3, 5}

    # Nothing is worked out on the request: the stored bytes go out as they are
    def no(*a):
        raise AssertionError("marked on the request")
    monkeypatch.setattr(tagmarks, "mark_original", no)
    monkeypatch.setattr(tagmarks, "mark_all", no)
    for number, stored in ((0, original), (1, primary)):
        r = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/{number}/tagmap")
        assert r.status_code == 200 and r.content == stored
        assert r.headers["content-type"] == "application/json" and r.headers["content-length"] == str(len(stored))
    assert all(e.get("orig") for e in json.loads(original)["elements"])  # the upload's own tags: all of them


def test_a_saved_version_is_stored_with_its_marks(setup):
    s, db = setup, setup["db"]
    job_id = _kept_tags_job(s)
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions",
                            json={"based_on": 1, "edits": [{"op": "set_role", "element": 3, "expect_role": "P", "role": "H2"}]})
    assert r.status_code == 201, r.text
    number = r.json()["number"]
    worker.submit_versions(db)
    ver = db.scalar(select(DocumentVersion).where(DocumentVersion.job_id == uuid.UUID(job_id),
                                                  DocumentVersion.number == number))
    s["engine"].complete_edit(ver.engine_job_id)
    worker.poll_versions(db)
    stored = _stored(db, job_id, number)
    assert stored.startswith(MARKED)
    assert {e["i"] for e in json.loads(stored)["elements"] if e.get("orig")} == {0, 1, 2, 3, 5}


def test_tags_not_kept_are_stored_and_served_as_the_engine_made_them(setup):
    s, db = setup, setup["db"]
    _engine_with_kept_tags(s)
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("tagged.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)  # no existing tags kept
    run_worker(db)
    stored = _stored(db, job_id, 1)
    assert not stored.startswith(MARKED) and json.loads(stored) == _remediated()
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/versions/1/tagmap").content == stored


def test_marked_maps_say_so_in_their_first_bytes():
    data = tagmarks.dump_marked(mark_all(_remediated()))
    assert tagmarks.is_marked(data[:len(MARKED)]) and json.loads(data)["orig_marked"] is True
    assert tagmarks.dump_marked(json.loads(data)) == data  # marking again doesn't add a second key
    assert not tagmarks.is_marked(json.dumps(_remediated()).encode())
