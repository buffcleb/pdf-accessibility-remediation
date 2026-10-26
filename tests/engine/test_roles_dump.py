"""harness/roles_dump.py: a dump of what the engine makes of each document (no text unless asked), and the comparisons
that say whether a change altered it."""
import json

import pymupdf as fitz

from harness import roles_dump


def _pdf(path, heading="Results"):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), heading, fontsize=20, fontname="hebo")
    for n in range(6):
        page.insert_text((72, 120 + 14 * n), "The survey covered forty sites across the region in spring.", fontsize=11)
    doc.save(path)
    return path


def test_a_dump_holds_roles_and_hashes_not_text(tmp_path):
    (tmp_path / "in").mkdir()
    _pdf(tmp_path / "in" / "a.pdf")
    assert roles_dump.dump(tmp_path / "in", tmp_path / "out") == 0
    data = json.loads((tmp_path / "out" / "a.json").read_text())
    blocks = data["pages"][0]["blocks"]
    assert blocks[0]["role"] == "H1" and len(blocks[0]["text"]) == 16
    assert "survey" not in json.dumps(data) and "Results" not in json.dumps(data)   # confidential text stays out
    assert roles_dump.dump(tmp_path / "in", tmp_path / "plain", with_text=True) == 0
    assert "Results" in (tmp_path / "plain" / "a.json").read_text()


def test_two_dumps_of_the_same_code_are_the_same_and_a_change_is_found(tmp_path, capsys):
    for side in ("before", "after", "changed"):
        (tmp_path / side).mkdir()
    _pdf(tmp_path / "before" / "a.pdf")
    _pdf(tmp_path / "after" / "a.pdf")
    _pdf(tmp_path / "changed" / "a.pdf", heading="Findings")
    for side in ("before", "after", "changed"):
        roles_dump.dump(tmp_path / side, tmp_path / f"d-{side}")
    assert roles_dump.diff(tmp_path / "d-before", tmp_path / "d-after") == 0
    assert "0 of 1 documents changed" in capsys.readouterr().out
    assert roles_dump.diff(tmp_path / "d-before", tmp_path / "d-changed") == 1
    out = capsys.readouterr().out
    assert "p0 p0b0 text" in out
    assert "Findings" not in out                                                  # which block, not what it says


def test_block_changes_are_named(tmp_path):
    block = {"id": "p0b1", "role": "P", "text": "a", "actual_text": None, "list_label": None, "note_mark": None,
             "note_kind": "footnote", "continues": None, "artifact_type": None, "notes": [], "mark_notes": [],
             "bbox": [1, 2, 3, 4]}
    page = {"number": 0, "columns": 1, "reading_order": ["p0b1"], "blocks": [block]}
    before = {"pages": [page]}
    after = {"pages": [{**page, "reading_order": ["p0b1", "p0b2"],
                        "blocks": [{**block, "role": "Note", "note_mark": "1"}, {**block, "id": "p0b2"}]}]}
    assert roles_dump.compare_documents(before, after) == [
        "p0 p0b2 added (P)", "p0 p0b1 role P -> Note, note_mark", "p0 reading order"]
    assert roles_dump.compare_documents(before, before) == []


def test_tag_maps_are_compared_by_place_in_the_tree(tmp_path, capsys):
    def tagmap(note_role):
        return {"elements": [{"i": 0, "parent": None, "role": "Document", "page": None, "text": ""},
                             {"i": 1, "parent": 0, "role": "P", "page": 1, "text": "Body"},
                             {"i": 2, "parent": 0, "role": note_role, "page": 1, "text": "1 Smith"}]}
    for side, role in (("a", "Note"), ("b", "Note"), ("c", "P")):
        (tmp_path / side / "doc1").mkdir(parents=True)
        (tmp_path / side / "doc1" / "doc_tagmap_primary.json").write_text(json.dumps(tagmap(role)))
    assert roles_dump.tagmaps(tmp_path / "a", tmp_path / "b") == 0
    assert roles_dump.tagmaps(tmp_path / "a", tmp_path / "c") == 1
    out = capsys.readouterr().out
    assert "Document[0]/Note[0] removed" in out and "Document[0]/P[1] added" in out
