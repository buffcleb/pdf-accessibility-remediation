"""Which tags were in the document as uploaded (shown in the tag manager when its tags were kept)."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from portal.models import DocumentVersion, Job, JobFile
from portal.storage import get_storage
from portal.tagmarks import mark_all, mark_original
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
    f = db.get(JobFile, version.tagmap_file_id)
    get_storage().put_bytes(f.s3_key, json.dumps(tagmap).encode(), "application/json")


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
