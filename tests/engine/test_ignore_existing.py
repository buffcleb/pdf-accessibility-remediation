"""#396: a tagged PDF whose tags are poor can be tagged afresh from the page, its own tags set aside, at the person's
request. Without the request, usable tags are kept (and repaired) as before."""
from __future__ import annotations

from app.pipeline.orchestrator import JobOptions, prepare_analysis
from app.tagging.tagger import tag_document


def test_existing_tags_are_kept_unless_the_person_asks_for_them_to_be_ignored(samples, tmp_path):
    src = samples / "partially_tagged.pdf"
    analysis = prepare_analysis(src)
    kept = tag_document(src, tmp_path / "kept.pdf", analysis, title="T", language="en-US")
    assert kept.existing_tags["present"] and kept.existing_tags["usable"] and not kept.existing_tags["rebuilt_tree"]
    fresh = tag_document(src, tmp_path / "fresh.pdf", prepare_analysis(src), title="T", language="en-US",
                         ignore_existing=True)
    assert fresh.existing_tags["present"] and fresh.existing_tags["rebuilt_tree"]
    assert fresh.existing_tags["reason"] == "ignored at your request"


def test_the_option_comes_from_the_jobs_flags():
    assert JobOptions.from_meta({"flags": {"ignore_existing": True}}).ignore_existing is True
    assert JobOptions.from_meta({"flags": {}}).ignore_existing is False
