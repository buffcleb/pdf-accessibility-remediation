"""Tag maps and tag edits: every edit keeps the tag tree valid and the pages pixel-identical."""
import pikepdf
import pymupdf as fitz
import pytest

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.editor import EditError, apply_edits, validate_ops
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _tag(samples, tmp_path_factory, name):
    out = tmp_path_factory.mktemp("edit") / f"{name}_tagged.pdf"
    analysis = prepare_analysis(samples / f"{name}.pdf")
    for p in analysis.pages:
        for i in p.images:
            i.alt = "Bar chart of remediation progress by quarter"
    tag_document(samples / f"{name}.pdf", out, analysis, title="T", language="en-US")
    return out


@pytest.fixture(scope="module")
def report(samples, tmp_path_factory):
    return _tag(samples, tmp_path_factory, "simple_report")


@pytest.fixture(scope="module")
def table(samples, tmp_path_factory):
    return _tag(samples, tmp_path_factory, "table_doc")


def _errors(path):
    with pikepdf.open(path) as pdf:
        findings, _ = check_integrity(pdf)
    return [f for f in findings if f.severity == "error"]


def _pixels(path):
    with fitz.open(path) as doc:
        return [p.get_pixmap(dpi=60).samples for p in doc]


def _first(tm, role):
    return next(e for e in tm["elements"] if e["role"] == role)


def _edit(src, tmp_path, ops):
    dst = tmp_path / "edited.pdf"
    log = apply_edits(src, dst, ops)
    assert _errors(dst) == [], "edit broke the tag tree"
    assert _pixels(dst) == _pixels(src), "edit changed how the page looks"
    return dst, log


# --- tag map -------------------------------------------------------------------------------------

def test_tagmap_covers_content_with_boxes_inside_pages(report):
    tm = build_tagmap(report)
    assert tm["schema"] == 1 and len(tm["pages"]) >= 1
    with_content = [e for e in tm["elements"] if e["own"]]
    assert with_content, "no content-bearing elements"
    assert [e["order"] for e in with_content] == list(range(len(with_content)))
    for e in tm["elements"]:
        for b in e["boxes"]:
            page = tm["pages"][b["page"] - 1]
            x0, y0, x1, y1 = b["bbox"]
            assert -1 <= x0 <= x1 <= page["w"] + 1 and -1 <= y0 <= y1 <= page["h"] + 1
    fig = _first(tm, "Figure")
    assert fig["alt"].startswith("Bar chart")


def test_tagmap_indices_match_verification_findings(report):
    """Findings name elements by index; the tag map must use the same numbering."""
    with pikepdf.open(report) as pdf:
        _, index = check_integrity(pdf)
    tm = build_tagmap(report)
    assert [e["role"] for e in tm["elements"]] == [e.role for e in index.elements]


def test_tagmap_of_untagged_file_is_empty(samples):
    tm = build_tagmap(samples / "simple_report.pdf")
    assert tm["elements"] == [] and tm["pages"]


# --- edits ---------------------------------------------------------------------------------------

def test_set_role_and_heading_level(report, tmp_path):
    tm = build_tagmap(report)
    p = _first(tm, "P")
    dst, log = _edit(report, tmp_path, [{"op": "set_role", "element": p["i"], "expect_role": "P", "role": "H2"}])
    assert build_tagmap(dst)["elements"][p["i"]]["role"] == "H2"
    assert "P -> H2" in log[0]


def test_set_and_remove_alt_and_lang(report, tmp_path):
    tm = build_tagmap(report)
    fig, p = _first(tm, "Figure"), _first(tm, "P")
    dst, _ = _edit(report, tmp_path, [
        {"op": "set_alt", "element": fig["i"], "expect_role": "Figure", "alt": "Revenue rose 40% from Q1 to Q4"},
        {"op": "set_lang", "element": p["i"], "expect_role": "P", "lang": "fr-CA"},
    ])
    out = build_tagmap(dst)["elements"]
    assert out[fig["i"]]["alt"] == "Revenue rose 40% from Q1 to Q4"
    assert out[p["i"]]["lang"] == "fr-CA"
    dst2 = tmp_path / "again.pdf"
    apply_edits(dst, dst2, [{"op": "set_lang", "element": p["i"], "expect_role": "P", "lang": ""}])
    assert "lang" not in build_tagmap(dst2)["elements"][p["i"]]


def test_decorative_removes_tag_and_artifacts_content(report, tmp_path):
    tm = build_tagmap(report)
    fig = _first(tm, "Figure")
    dst, log = _edit(report, tmp_path, [{"op": "set_decorative", "element": fig["i"], "expect_role": "Figure"}])
    after = build_tagmap(dst)["elements"]
    assert len(after) == len(tm["elements"]) - 1
    figures = lambda els: sum(1 for e in els if e["role"] == "Figure")  # noqa: E731
    assert figures(after) == figures(tm["elements"]) - 1
    assert "artifact" in log[0]


def test_reorder_moves_reading_order(report, tmp_path):
    tm = build_tagmap(report)
    ps = [e for e in tm["elements"] if e["role"] == "P" and e["own"]]
    first, second = ps[0], ps[1]
    dst, _ = _edit(report, tmp_path, [
        {"op": "move", "element": second["i"], "expect_role": "P", "target": first["i"], "position": "before"}])
    order = [e["text"] for e in build_tagmap(dst)["elements"] if e["role"] == "P" and e["own"]]
    assert order.index(second["text"]) < order.index(first["text"])


def test_table_cell_header_and_scope(table, tmp_path):
    tm = build_tagmap(table)
    td = _first(tm, "TD")
    dst, _ = _edit(table, tmp_path, [
        {"op": "set_cell", "element": td["i"], "expect_role": "TD", "role": "TH", "scope": "Row"}])
    cell = build_tagmap(dst)["elements"][td["i"]]
    assert cell["role"] == "TH" and cell["scope"] == "Row"
    dst2 = tmp_path / "back.pdf"
    apply_edits(dst, dst2, [{"op": "set_cell", "element": td["i"], "expect_role": "TH", "role": "TD"}])
    back = build_tagmap(dst2)["elements"][td["i"]]
    assert back["role"] == "TD" and "scope" not in back


def test_edits_refuse_stale_or_unsafe_requests(report, tmp_path):
    tm = build_tagmap(report)
    p = _first(tm, "P")
    with pytest.raises(EditError, match="not a Figure"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "set_alt", "element": p["i"], "expect_role": "Figure", "alt": "x"}])
    with pytest.raises(EditError, match="doesn't exist"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "set_role", "element": 99999, "role": "P"}])
    doc = tm["elements"][0]
    with pytest.raises(EditError, match="inside itself"):
        apply_edits(report, tmp_path / "x.pdf", [
            {"op": "move", "element": doc["i"], "target": doc["kids"][0], "position": "after"}])
    with pytest.raises(EditError, match="standard"):
        validate_ops([{"op": "set_role", "element": 1, "role": "Banana"}])
    with pytest.raises(EditError, match="language"):
        validate_ops([{"op": "set_lang", "element": 1, "lang": "not a lang!"}])
    with pytest.raises(EditError, match="scope"):
        validate_ops([{"op": "set_cell", "element": 1, "role": "TH"}])


def test_merge_joins_content_into_one_tag(report, tmp_path):
    tm = build_tagmap(report)
    ps = [e for e in tm["elements"] if e["role"] == "P" and e["own"]]
    a, b = ps[0], ps[1]
    dst, log = _edit(report, tmp_path, [
        {"op": "merge", "element": a["i"], "expect_role": "P", "absorb": [b["i"]]}])
    after = build_tagmap(dst)["elements"]
    assert len(after) == len(tm["elements"]) - 1
    merged = after[a["i"]]  # b came after a, so a keeps its index
    assert merged["role"] == "P"
    assert any(p.get("text", "").startswith(b["text"][:15]) for p in merged["parts"])
    starts_b = lambda els: sum(1 for e in els if e["own"] and (e.get("text") or "").startswith(b["text"][:20]))  # noqa: E731
    assert starts_b(after) == starts_b(tm["elements"]) - 1
    assert "merged" in log[0]


def test_split_makes_two_tags_of_the_same_type(report, tmp_path):
    tm = build_tagmap(report)
    target = next(e for e in tm["elements"] if e.get("parts") and len(e["parts"]) >= 2)
    dst, log = _edit(report, tmp_path, [
        {"op": "split", "element": target["i"], "expect_role": target["role"], "at": 1}])
    after = build_tagmap(dst)["elements"]
    assert len(after) == len(tm["elements"]) + 1
    assert sum(1 for e in after if e["role"] == target["role"]) == sum(
        1 for e in tm["elements"] if e["role"] == target["role"]) + 1
    assert "split" in log[0]


def test_merge_then_split_round_trip_stays_valid(report, tmp_path):
    tm = build_tagmap(report)
    ps = [e for e in tm["elements"] if e["role"] == "P" and e["own"]]
    dst, _ = _edit(report, tmp_path, [
        {"op": "merge", "element": ps[0]["i"], "expect_role": "P", "absorb": [ps[1]["i"], ps[2]["i"]]}])
    mid = build_tagmap(dst)
    merged = next(e for e in mid["elements"] if e.get("parts") and e["role"] == "P" and len(e["parts"]) >= 3)
    out = tmp_path / "split.pdf"
    apply_edits(dst, out, [{"op": "split", "element": merged["i"], "expect_role": "P", "at": len(merged["parts"]) - 1}])
    assert _errors(out) == []
    assert _pixels(out) == _pixels(report)


def test_merge_refuses_nesting(report, tmp_path):
    tm = build_tagmap(report)
    root = tm["elements"][0]
    with pytest.raises(EditError, match="contains it"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "merge", "element": root["i"], "absorb": [root["kids"][0]]}])
    with pytest.raises(EditError, match="merge"):
        validate_ops([{"op": "merge", "element": 1, "absorb": []}])


def test_split_inside_text_at_any_character(report, tmp_path):
    tm = build_tagmap(report)
    p = next(e for e in tm["elements"] if e["role"] == "P" and e["own"] and not e.get("parts")
             and len(e.get("text") or "") > 60 and not e.get("actual_text"))
    text = p["text"]  # the tag map shortens long text; the split point only needs to be within it
    char = text.index(" ", 12) + 1  # start of a word, partway in
    dst, log = _edit(report, tmp_path, [{"op": "split", "element": p["i"], "expect_role": "P", "at": 0, "char": char}])
    assert "inside its text" in log[0]
    texts = [e.get("text") or "" for e in build_tagmap(dst)["elements"] if e["role"] == "P"]
    k = texts.index(text[:char].strip())
    rest = text[char:].strip()
    assert texts[k + 1].startswith(rest[:40]), texts[k: k + 2]

    # Mid-word works too, and a part can be split again
    second = next(e for e in build_tagmap(dst)["elements"] if (e.get("text") or "").startswith(rest[:40]))
    (tmp_path / "again").mkdir()
    dst2, _ = _edit(dst, tmp_path / "again", [{"op": "split", "element": second["i"], "at": 0, "char": 3}])
    again = [e.get("text") or "" for e in build_tagmap(dst2)["elements"] if e["role"] == "P"]
    assert rest[:3] in again and any(t.startswith(rest[3:30].strip()) for t in again), again[:4]


def test_split_inside_text_refuses_the_ends(report, tmp_path):
    tm = build_tagmap(report)
    p = next(e for e in tm["elements"] if e["role"] == "P" and e["own"] and not e.get("parts") and e.get("text"))
    with pytest.raises(EditError, match="inside the text"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "split", "element": p["i"], "at": 0, "char": 100000}])
    with pytest.raises(EditError):
        validate_ops([{"op": "split", "element": p["i"], "at": 0, "char": 0}])


def _list_doc(tmp_path, items):
    """A tagged page with one list of the given items (strings; a list nests a sub-list under the previous)."""
    def draw(p):
        y = 100
        for it in items:
            for line in ([it] if isinstance(it, str) else it):
                x = 97 if isinstance(it, str) else 127
                p.insert_text((x, y), line, fontsize=11, fontname="tiro")
                y += 18
        p.insert_text((97, y + 30), "A closing paragraph of ordinary body text on the page.", fontsize=11,
                      fontname="tiro")
    doc = fitz.open()
    draw(doc.new_page(width=595, height=842))
    src = tmp_path / "list_src.pdf"
    doc.save(src)
    out = tmp_path / "list_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    return out


def _roles_under(tm, i):
    els = tm["elements"]
    out = []

    def walk(k, d):
        out.append((d, els[k]["role"], (els[k].get("text") or "")[:30]))
        for c in els[k]["kids"]:
            walk(c, d + 1)
    walk(i, 0)
    return out


def test_list_changed_to_paragraph_collapses_into_one_tag_per_item(tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text", ["(a) nested part"]])
    tm = build_tagmap(src)
    lst = next(e for e in tm["elements"] if e["role"] == "L")
    dst, log = _edit(src, tmp_path, [{"op": "set_role", "element": lst["i"], "expect_role": "L", "role": "P"}])
    assert "one per item" in log[0]
    after = build_tagmap(dst)
    roles = [e["role"] for e in after["elements"]]
    assert roles.count("LI") == 1 and roles.count("LBody") == 1  # only the nested list's item is left
    ps = [e for e in after["elements"] if e["role"] == "P" and (e.get("text") or "").startswith(("1.", "2."))]
    assert [p["text"][:2] for p in ps] == ["1.", "2."]
    assert not any(after["elements"][k]["role"] in ("LI", "LBody") for p in ps for k in p["kids"])
    nested = next(e for e in after["elements"] if e["role"] == "L")
    assert after["elements"][nested["parent"]]["role"] not in ("P", "LBody")  # moved out beside the items


def test_paragraph_changed_to_list_is_wrapped(tmp_path):
    src = _list_doc(tmp_path, ["1. Only item"])
    tm = build_tagmap(src)
    p = next(e for e in tm["elements"] if e["role"] == "P" and "closing" in (e.get("text") or ""))
    dst, log = _edit(src, tmp_path, [{"op": "set_role", "element": p["i"], "expect_role": "P", "role": "L"}])
    after = build_tagmap(dst)
    l = next(e for e in after["elements"] if e["role"] == "L" and "closing" in (e.get("text") or ""))
    li = after["elements"][l["kids"][0]]
    assert li["role"] == "LI" and after["elements"][li["kids"][0]]["role"] == "LBody"


def test_impossible_role_changes_are_refused(report, tmp_path):
    tm = build_tagmap(report)
    p = next(e for e in tm["elements"] if e["role"] == "P")
    sect = next((e for e in tm["elements"] if e["role"] in ("Sect", "Document", "Div") and any(
        tm["elements"][k]["role"] == "P" for k in e["kids"])), None)
    with pytest.raises(EditError, match="has to be inside"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "set_role", "element": p["i"], "role": "LI"}])
    with pytest.raises(EditError, match="has to be inside"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "set_role", "element": p["i"], "role": "TR"}])
    if sect is not None:
        with pytest.raises(EditError, match="holds other blocks"):
            apply_edits(report, tmp_path / "x.pdf", [{"op": "set_role", "element": sect["i"], "role": "P"}])
