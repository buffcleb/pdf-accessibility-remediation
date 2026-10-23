"""#428: an edit job publishes its file and tag map as soon as they're final, before veraPDF and the pixel
comparison, so the saved version can be opened while it's still being checked."""
import asyncio
import json
import uuid

from app.pipeline import edit_job
from app.pipeline.analyzer import analyze_document
from app.storage import JobStore
from app.tagging.tagger import tag_document
from tests.engine.test_api import client  # noqa: F401 - the fixture
from tests.engine.test_faster_checks import _pages


def _store(tmp_path, monkeypatch):
    src = _pages(tmp_path / "src.pdf", n=3, faint=())
    tagged = tmp_path / "tagged.pdf"
    tag_document(src, tagged, analyze_document(src), title="Pages", language="en-US")
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    store.input_pdf.write_bytes(tagged.read_bytes())
    store.write_meta({"job_id": jid, "kind": "edit", "stem": "doc", "verify_ai": False, "review": "none",
                      "edits": [{"op": "set_doc_title", "title": "Pages, edited"}], "flags": {}})
    return store


def test_the_file_and_tag_map_are_published_before_the_checks(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    seen = {}
    real = edit_job._validate

    def validate(*a, **k):  # veraPDF: by now the version could already be opened
        seen["partial"] = store.read_partial()
        seen["result"] = store.read_result()
        return real(*a, **k)

    monkeypatch.setattr(edit_job, "_validate", validate)
    result = asyncio.run(edit_job.run_edit(store, lambda s, p: None))
    partial = seen["partial"]
    assert seen["result"] is None
    assert partial["files"]["remediated_primary"] == result["files"]["remediated_primary"]
    assert partial["tag_map"] == result["reports"]["tag_maps"]["primary"]
    assert store.output_file(partial["tag_map"]) and store.output_file(partial["files"]["remediated_primary"])


def test_the_job_says_what_it_already_has_while_it_works(client, tmp_path):  # noqa: F811
    jid = str(uuid.uuid4())
    store = JobStore(jid)
    store.create()
    store.write_meta({"job_id": jid, "kind": "edit", "stem": "doc"})
    body = client.get(f"/api/v1/jobs/{jid}").json()
    assert body["status"] == "processing" and "partial" not in body
    (store.output_dir / "doc_reviewed.pdf").write_bytes(b"%PDF-1.7")
    (store.output_dir / "doc_tagmap_primary.json").write_text(json.dumps({"schema": 1}))
    store.write_partial({"files": {"remediated_primary": "doc_reviewed.pdf"}, "tag_map": "doc_tagmap_primary.json"})
    body = client.get(f"/api/v1/jobs/{jid}").json()
    assert body["status"] == "processing"
    assert body["partial"] == {"files": {"remediated_primary": f"/api/v1/jobs/{jid}/files/doc_reviewed.pdf"},
                               "tag_map": f"/api/v1/jobs/{jid}/files/doc_tagmap_primary.json"}
    assert client.get(body["partial"]["tag_map"]).json() == {"schema": 1}
