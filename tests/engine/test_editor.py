"""Tag maps and tag edits: every edit keeps the tag tree valid and the pages pixel-identical."""
from collections import Counter

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


def test_the_documents_title_and_language_are_edited_without_an_element(report, tmp_path):
    """#363: the title a reader announces when the file opens, and the document's language, everywhere it's kept."""
    assert build_tagmap(report)["document"] == {"title": "T", "lang": "en-US"}
    dst, log = _edit(report, tmp_path, [{"op": "set_doc_title", "title": "  Quarterly progress report  "},
                                        {"op": "set_doc_lang", "lang": "fr-CA"}])
    assert log == ['document title set to "Quarterly progress report"', "document language set to fr-CA"]
    assert build_tagmap(dst)["document"] == {"title": "Quarterly progress report", "lang": "fr-CA"}
    with pikepdf.open(dst) as pdf:
        assert str(pdf.docinfo["/Title"]) == "Quarterly progress report"
        assert pdf.Root.ViewerPreferences.DisplayDocTitle is True
        with pdf.open_metadata() as meta:
            assert meta["dc:title"] == "Quarterly progress report" and set(meta["dc:language"]) == {"fr-CA"}
            assert meta["pdfuaid:part"] == "1"  # the rest of the XMP stays
    for bad in ({"op": "set_doc_title", "title": "  "}, {"op": "set_doc_title", "title": "x" * 501},
                {"op": "set_doc_lang", "lang": ""}, {"op": "set_doc_lang", "lang": "not a language"}):
        with pytest.raises(EditError):
            validate_ops([bad])


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
    first = ps[0]
    second = next(e for e in ps[1:] if e["text"] != first["text"])  # one that can be told apart from the first
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


# --- one list from several tags, and list levels -------------------------------------------------------

NOTES = ["Bring the water to a rolling boil in the large pot before anything else goes in.",
         "Stir the grains in slowly so that none of them stick to the bottom of the pot.",
         "Lower the heat and leave the lid slightly open for about twenty minutes.",
         "Take the pot off the stove and let it rest covered until the table is set."]


def _notes_doc(tmp_path):
    """A tagged page of four separate paragraphs."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k, line in enumerate(NOTES):
        page.insert_text((72, 100 + 60 * k), line, fontsize=11, fontname="tiro")
    src = tmp_path / "notes_src.pdf"
    doc.save(src)
    out = tmp_path / "notes_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    return out


def _notes(tm):
    found = [next(e for e in tm["elements"] if e["role"] == "P" and (e.get("text") or "").startswith(n[:20])) for n in NOTES]
    assert len({e["i"] for e in found}) == len(NOTES)
    return found


def _outline(tm):
    """Each list item's depth (how many lists it is inside) and the start of its text, in reading order."""
    els = tm["elements"]
    out = []

    def walk(k, depth):
        e = els[k]
        if e["role"] == "LI":
            body = next((els[c] for c in e["kids"] if els[c]["role"] == "LBody"), None)
            out.append((depth, ((body or e).get("text") or "")[:12]))
        for c in e["kids"]:
            walk(c, depth + (e["role"] == "L"))
    walk(0, 0)
    return out


def _starts(*pairs):
    return [(d, NOTES[k][:12]) for d, k in pairs]


def test_several_paragraphs_become_one_list(tmp_path):
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    dst, log = _edit(src, tmp_path, [{"op": "make_list", "element": a["i"], "expect_role": "P", "absorb": [b["i"], c["i"]]}])
    assert "one list made of 3 tags" in log[0]
    after = build_tagmap(dst)
    assert [e["role"] for e in after["elements"]].count("L") == 1
    assert _outline(after) == _starts((1, 0), (1, 1), (1, 2))
    lst = _first(after, "L")
    assert [after["elements"][k]["role"] for k in lst["kids"]] == ["LI", "LI", "LI"]
    # the fourth paragraph is untouched and still read after the list
    last = next(e for e in after["elements"] if e["role"] == "P")
    assert last["text"].startswith(NOTES[3][:20]) and last["i"] > lst["i"]


def test_a_list_among_the_selected_tags_gives_its_items(tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text"])
    tm = build_tagmap(src)
    lst = _first(tm, "L")
    closing = next(e for e in tm["elements"] if e["role"] == "P" and "closing" in (e.get("text") or ""))
    dst, _ = _edit(src, tmp_path, [{"op": "make_list", "element": lst["i"], "expect_role": "L", "absorb": [closing["i"]]}])
    after = build_tagmap(dst)
    roles = [e["role"] for e in after["elements"]]
    assert roles.count("L") == 1 and roles.count("LI") == 3 and "P" not in roles
    assert [t[:9] for _, t in _outline(after)] == ["1. First ", "2. Second", "A closing"]


def test_items_of_a_new_list_can_be_indented_in_the_same_save(tmp_path):
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    ops = [{"op": "make_list", "element": a["i"], "expect_role": "P", "absorb": [b["i"], c["i"], d["i"]]},
           {"op": "indent", "element": b["i"], "expect_role": "P"},
           {"op": "indent", "element": c["i"], "expect_role": "P"}]
    dst, log = _edit(src, tmp_path, ops)
    assert "one level in" in log[1]
    after = build_tagmap(dst)
    assert _outline(after) == _starts((1, 0), (2, 1), (2, 2), (1, 3))
    # both went into one nested list, inside the first item's body after its text
    nested = [e for e in after["elements"] if e["role"] == "L"][1]
    assert after["elements"][nested["parent"]]["role"] == "LBody" and len(nested["kids"]) == 2


def test_outdent_takes_the_items_after_it_along_so_the_order_stays(tmp_path):
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    made = [{"op": "make_list", "element": a["i"], "expect_role": "P", "absorb": [b["i"], c["i"], d["i"]]}]
    nest = [{"op": "indent", "element": x["i"], "expect_role": "P"} for x in (b, c, d)]
    dst, _ = _edit(src, tmp_path, made + nest)
    assert _outline(build_tagmap(dst)) == _starts((1, 0), (2, 1), (2, 2), (2, 3))
    # the middle one of the three nested items goes back out: the one after it now sits under it
    dst2, log = _edit(src, tmp_path, made + nest + [{"op": "outdent", "element": c["i"], "expect_role": "P"}])
    assert "one level out" in log[-1]
    assert _outline(build_tagmap(dst2)) == _starts((1, 0), (2, 1), (1, 2), (2, 3))
    # and taking every nested item out again leaves a flat list with no empty list behind
    flat = made + nest + [{"op": "outdent", "element": x["i"], "expect_role": "P"} for x in (b, c, d)]
    dst3, _ = _edit(src, tmp_path, flat)
    after = build_tagmap(dst3)
    assert _outline(after) == _starts((1, 0), (1, 1), (1, 2), (1, 3))
    assert [e["role"] for e in after["elements"]].count("L") == 1


def test_indent_and_outdent_on_a_list_the_file_already_has(tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text", ["(a) nested part"], "3. Third item text"])
    tm = build_tagmap(src)
    assert [d for d, _ in _outline(tm)] == [1, 1, 2, 1]
    items = [e for e in tm["elements"] if e["role"] == "LI"]
    third, nested = items[3], items[2]
    dst, _ = _edit(src, tmp_path, [{"op": "indent", "element": third["i"], "expect_role": "LI"}])
    after = build_tagmap(dst)
    assert [d for d, _ in _outline(after)] == [1, 1, 2, 2]  # joined the list already nested under the second item
    assert [e["role"] for e in after["elements"]].count("L") == 2
    dst, _ = _edit(src, tmp_path, [{"op": "outdent", "element": nested["i"], "expect_role": "LI"}])
    after = build_tagmap(dst)
    assert [d for d, _ in _outline(after)] == [1, 1, 1, 1] and [e["role"] for e in after["elements"]].count("L") == 1
    assert [t[:3] for _, t in _outline(after)] == ["1. ", "2. ", "(a)", "3. "]


def test_list_edits_that_cant_work_are_refused(report, tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text"])
    tm = build_tagmap(src)
    first = next(e for e in tm["elements"] if e["role"] == "LI")
    closing = next(e for e in tm["elements"] if e["role"] == "P")
    lst = _first(tm, "L")
    for ops, why in [
        ([{"op": "indent", "element": first["i"], "expect_role": "LI"}], "no item before it"),
        ([{"op": "outdent", "element": first["i"], "expect_role": "LI"}], "already at the top level"),
        ([{"op": "indent", "element": closing["i"], "expect_role": "P"}], "only a list item"),
        ([{"op": "make_list", "element": lst["i"], "expect_role": "L", "absorb": [first["i"]]}], "can't become a list item"),
    ]:
        with pytest.raises(EditError, match=why):
            apply_edits(src, tmp_path / "x.pdf", ops)
    fig = _first(build_tagmap(report), "Figure")
    para = _first(build_tagmap(report), "P")
    with pytest.raises(EditError, match="can't become a list item"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "make_list", "element": para["i"], "expect_role": "P", "absorb": [fig["i"]]}])
    for bad in ({"op": "make_list", "element": 3}, {"op": "make_list", "element": 3, "absorb": [3]},
                {"op": "make_list", "element": 3, "absorb": []}):
        with pytest.raises(EditError, match="making a list"):
            validate_ops([bad])


# --- a list made into a table of contents --------------------------------------------------------------

def test_a_list_becomes_a_table_of_contents_with_its_nested_entries(tmp_path):
    src = _list_doc(tmp_path, ["1. Introduction to the study", "2. Methods we used", ["(a) Sampling the sites"],
                               "3. Results and findings"])
    tm = build_tagmap(src)
    dst, log = _edit(src, tmp_path, [{"op": "make_toc", "element": _first(tm, "L")["i"], "expect_role": "L"}])
    assert "table of contents of 4 entries" in log[0]
    after = build_tagmap(dst)
    assert not {"L", "LI", "LBody"} & {e["role"] for e in after["elements"]}
    assert [(d, r, t[:9]) for d, r, t in _roles_under(after, _first(after, "TOC")["i"])] == [
        (0, "TOC", "1. Introd"),
        (1, "TOCI", "1. Introd"), (2, "Reference", "1. Introd"),
        (1, "TOCI", "2. Method"), (2, "Reference", "2. Method"),
        (2, "TOC", "(a) Sampl"), (3, "TOCI", "(a) Sampl"), (4, "Reference", "(a) Sampl"),
        (1, "TOCI", "3. Result"), (2, "Reference", "3. Result")]
    with pikepdf.open(dst) as pdf:  # bullet style and numbering are list attributes; a TOC has none
        toc = next(o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and o.get("/S") == "/TOC")
        assert "/A" not in toc


def test_a_link_in_an_entry_stays_inside_its_reference(tmp_path):
    src = _linked(tmp_path)
    tm = build_tagmap(src)
    paras = [e for e in tm["elements"] if e["role"] == "P"]
    listed, _ = _edit(src, tmp_path, [{"op": "make_list", "element": paras[0]["i"], "expect_role": "P",
                                       "absorb": [paras[1]["i"]]}])
    lst = _first(build_tagmap(listed), "L")
    dst = tmp_path / "toc.pdf"
    apply_edits(listed, dst, [{"op": "make_toc", "element": lst["i"], "expect_role": "L"}])
    assert _errors(dst) == []
    after = build_tagmap(dst)
    links = [e for e in after["elements"] if e["role"] == "Link"]
    assert len(links) == 2 and all(after["elements"][k["parent"]]["role"] == "Reference" for k in links)


def test_text_after_a_nested_list_gets_its_own_reference_so_the_order_stays(tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text", ["(a) nested part"], "3. Third item text"])
    # Write the second item's body as text, nested list, then more text (the third item's), as some files have it
    shaped = tmp_path / "shaped.pdf"
    with pikepdf.open(src) as pdf:
        bodies = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and o.get("/S") == "/LBody"]
        second, third = bodies[1], bodies[3]
        moved = list(third.K) if isinstance(third.K, pikepdf.Array) else [third.K]
        second.K = pikepdf.Array(list(second.K) + moved)
        item = third.P
        item.P.K = pikepdf.Array([k for k in item.P.K if k.objgen != item.objgen])
        pdf.save(shaped)
    tm = build_tagmap(shaped)
    dst, _ = _edit(shaped, tmp_path, [{"op": "make_toc", "element": _first(tm, "L")["i"], "expect_role": "L"}])
    after = build_tagmap(dst)
    entry = [e for e in after["elements"] if e["role"] == "TOCI"][1]
    assert [(after["elements"][k]["role"], after["elements"][k]["text"][:3]) for k in entry["kids"]] == [
        ("Reference", "2. "), ("TOC", "(a)"), ("Reference", "3. ")]


def test_making_a_table_of_contents_refuses_what_cant_work(tmp_path):
    src = _list_doc(tmp_path, ["1. First item text", "2. Second item text", ["(a) nested part"]])
    tm = build_tagmap(src)
    nested = [e for e in tm["elements"] if e["role"] == "L"][1]
    closing = next(e for e in tm["elements"] if e["role"] == "P")
    for ops, why in [([{"op": "make_toc", "element": nested["i"], "expect_role": "L"}], "nested in another one"),
                     ([{"op": "make_toc", "element": closing["i"], "expect_role": "P"}], "only a list")]:
        with pytest.raises(EditError, match=why):
            apply_edits(src, tmp_path / "x.pdf", ops)


def _one_paragraph_of_notes(tmp_path):
    """The four notes as one paragraph with four pieces of content, the way a tagger reads an outline."""
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    one = tmp_path / "notes_one.pdf"
    apply_edits(src, one, [{"op": "merge", "element": a["i"], "expect_role": "P", "absorb": [b["i"], c["i"], d["i"]]}])
    tm = build_tagmap(one)
    p = next(e for e in tm["elements"] if e["role"] == "P")
    assert [x["k"] for x in p["parts"]] == [0, 1, 2, 3]
    return one, p


def test_a_paragraph_is_built_into_a_heading_and_a_nested_list(tmp_path):
    src, p = _one_paragraph_of_notes(tmp_path)
    rows = [{"at": 0, "as": "H3"}, {"at": 1, "level": 1}, {"at": 2, "level": 2}, {"at": 3, "level": 1}]
    dst, log = _edit(src, tmp_path, [{"op": "build_list", "element": p["i"], "expect_role": "P", "rows": rows}])
    assert "3 list item(s) and 1 other tag(s)" in log[0]
    after = build_tagmap(dst)
    roles = [e["role"] for e in after["elements"]]
    assert "P" not in roles and roles.count("H3") == 1 and roles.count("L") == 2 and roles.count("LI") == 3
    assert _outline(after) == _starts((1, 1), (2, 2), (1, 3))
    head = _first(after, "H3")
    assert head["text"].startswith(NOTES[0][:20]) and head["i"] == p["i"]      # the tag itself became the first row
    assert head["i"] < _first(after, "L")["i"]                                   # and the list follows it
    nested = [e for e in after["elements"] if e["role"] == "L"][1]
    assert after["elements"][nested["parent"]]["role"] == "LBody"


def test_rows_can_hold_several_pieces_and_all_be_list_items(tmp_path):
    src, p = _one_paragraph_of_notes(tmp_path)
    rows = [{"at": 0, "level": 1}, {"at": 2, "level": 2}]                        # two pieces each: a wrapped line
    dst, _ = _edit(src, tmp_path, [{"op": "build_list", "element": p["i"], "expect_role": "P", "rows": rows}])
    after = build_tagmap(dst)
    assert [d for d, _ in _outline(after)] == [1, 2]
    bodies = [e for e in after["elements"] if e["role"] == "LBody"]
    assert NOTES[1][:20] in bodies[0]["text"] and NOTES[3][:20] in bodies[1]["text"]
    assert after["elements"][p["i"]]["role"] == "L"   # the list took the paragraph's place


def test_several_tags_are_built_into_one_list_with_a_paragraph_between(tmp_path):
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    rows = [{"at": 0, "level": 1}, {"el": b["i"], "at": 0, "level": 2}, {"el": c["i"], "at": 0, "as": "P"},
            {"el": d["i"], "at": 0, "level": 1}]
    dst, _ = _edit(src, tmp_path, [{"op": "build_list", "element": a["i"], "expect_role": "P",
                                    "absorb": [b["i"], c["i"], d["i"]], "rows": rows}])
    after = build_tagmap(dst)
    # a paragraph between ends the first list; the fourth note starts a new one
    assert _outline(after) == _starts((1, 0), (2, 1), (1, 3))
    kinds = [e["role"] for e in after["elements"] if e["role"] in ("L", "P") and after["elements"][e["parent"]]["role"] not in ("LBody", "LI", "L")]
    assert kinds == ["L", "P", "L"]


def test_building_a_list_refuses_rows_that_cant_work(report, tmp_path):
    src, p = _one_paragraph_of_notes(tmp_path)
    op = lambda rows, **more: [{"op": "build_list", "element": p["i"], "expect_role": "P", "rows": rows, **more}]  # noqa: E731
    for rows, why in [
        ([{"at": 0, "level": 2}], "one level deeper"),
        ([{"at": 0, "level": 1}, {"at": 1, "level": 3}], "one level deeper"),
        ([{"at": 1, "level": 1}], "don't match"),                               # the first row starts at the start
        ([{"at": 0, "level": 1}, {"at": 2, "level": 1}, {"at": 1, "level": 1}], "don't match"),
        ([{"at": 0, "level": 1}, {"at": 9, "level": 1}], "don't match"),
    ]:
        with pytest.raises(EditError, match=why):
            apply_edits(src, tmp_path / "x.pdf", op(rows))
    for bad in ([], [{"at": 0}], [{"at": 0, "level": 1, "as": "P"}], [{"at": 0, "as": "Figure"}], [{"at": 0, "level": 0}],
                [{"at": 0, "level": 1}, {"el": 999, "at": 0, "level": 1}]):
        with pytest.raises(EditError, match="row"):
            validate_ops(op(bad))
    fig = _first(build_tagmap(report), "Figure")
    with pytest.raises(EditError, match="can't be built into a list"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "build_list", "element": fig["i"], "expect_role": "Figure",
                                                  "rows": [{"at": 0, "level": 1}]}])
    # read-aloud text stands for the whole paragraph, so it can't be cut up
    spoken = tmp_path / "spoken.pdf"
    apply_edits(src, spoken, [{"op": "set_actual_text", "element": p["i"], "expect_role": "P", "text": "All four notes"}])
    with pytest.raises(EditError, match="stands for all of it"):
        apply_edits(spoken, tmp_path / "x.pdf", op([{"at": 0, "level": 1}, {"at": 2, "level": 1}]))


def _figure_of_four(tmp_path):
    """One figure holding four pieces of content, as a scheme of several drawings is tagged."""
    src, p = _one_paragraph_of_notes(tmp_path)
    fig = tmp_path / "figure_of_four.pdf"
    apply_edits(src, fig, [{"op": "set_role", "element": p["i"], "expect_role": "P", "role": "Figure"},
                           {"op": "set_alt", "element": p["i"], "expect_role": "P", "alt": "Four steps of a method"}])
    tm = build_tagmap(fig)
    f = next(e for e in tm["elements"] if e["role"] == "Figure")
    pieces = [x for x in tm["pieces"] if x["el"] == f["i"]]
    assert len(pieces) == 4
    return fig, f, pieces


def test_pieces_of_a_figure_become_a_figure_of_their_own(tmp_path):
    src, f, pieces = _figure_of_four(tmp_path)
    take = [{"page": x["page"], "mcid": x["mcid"]} for x in pieces[2:]]
    dst, log = _edit(src, tmp_path, [{"op": "take_pieces", "element": f["i"], "expect_role": "Figure", "pieces": take,
                                      "role": "Figure", "alt": "The last two steps"}])
    assert "2 piece(s) of content made into a new Figure" in log[0]
    after = build_tagmap(dst)
    figs = [e for e in after["elements"] if e["role"] == "Figure"]
    assert [e["alt"] for e in figs] == ["Four steps of a method", "The last two steps"]     # the new one is read next
    assert after["elements"][figs[0]["parent"]]["i"] == after["elements"][figs[1]["parent"]]["i"]
    owned = lambda e: sorted(x["mcid"] for x in after["pieces"] if x["el"] == e["i"])  # noqa: E731
    assert owned(figs[1]) == sorted(x["mcid"] for x in pieces[2:]) and owned(figs[0]) == sorted(x["mcid"] for x in pieces[:2])


def test_a_figure_can_be_split_into_several_in_one_save_and_text_taken_out_of_it(tmp_path):
    src, f, pieces = _figure_of_four(tmp_path)
    one = lambda x: [{"page": x["page"], "mcid": x["mcid"]}]  # noqa: E731
    dst, _ = _edit(src, tmp_path, [
        {"op": "take_pieces", "element": f["i"], "expect_role": "Figure", "pieces": one(pieces[1]), "role": "Figure", "alt": "Step two"},
        {"op": "take_pieces", "element": f["i"], "expect_role": "Figure", "pieces": one(pieces[3]), "role": "Caption"},
    ])
    after = build_tagmap(dst)
    roles = [e["role"] for e in after["elements"] if e["role"] in ("Figure", "Caption")]
    assert sorted(roles) == ["Caption", "Figure", "Figure"]


def test_taking_pieces_refuses_what_cant_work(tmp_path):
    src, f, pieces = _figure_of_four(tmp_path)
    all_of_it = [{"page": x["page"], "mcid": x["mcid"]} for x in pieces]
    op = lambda **o: [{"op": "take_pieces", "element": f["i"], "expect_role": "Figure", "pieces": all_of_it[:1], "role": "Figure", "alt": "x", **o}]  # noqa: E731
    with pytest.raises(EditError, match="take everything"):
        apply_edits(src, tmp_path / "x.pdf", op(pieces=all_of_it))
    with pytest.raises(EditError, match="isn't part of this Figure"):
        apply_edits(src, tmp_path / "x.pdf", op(pieces=[{"page": 1, "mcid": 999}]))
    for bad, why in [({"alt": ""}, "needs alt text"), ({"role": "Table"}, "new tag can be"), ({"pieces": []}, "needs the pieces")]:
        with pytest.raises(EditError, match=why):
            validate_ops(op(**bad))


def _coarse_scheme(tmp_path):
    """A tagged page whose figure is one marked section holding a whole scheme (two ring structures, an arrow, two
    labels), as some producers tag one: a single piece for all of it."""
    import math
    from pikepdf import Array, Dictionary, Name, String

    def ring(cx, cy, r=18):
        pts = [(cx + r * math.cos(math.radians(60 * k + 30)), cy + r * math.sin(math.radians(60 * k + 30))) for k in range(6)]
        return f"{pts[0][0]:.1f} {pts[0][1]:.1f} m " + " ".join(f"{x:.1f} {y:.1f} l" for x, y in pts[1:]) + " h S\n"
    content = ("/P <</MCID 0>> BDC BT /F1 11 Tf 72 780 Td (A paragraph above the scheme.) Tj ET EMC\n"
               "/Figure <</MCID 1>> BDC\n" + ring(150, 600) + ring(181, 582)
               + "BT /F1 9 Tf 200 612 Td (OH) Tj ET\n240 590 m 300 590 l S 292 595 m 300 590 l 292 585 l S\n"
               + ring(350, 600) + ring(381, 582) + "BT /F1 9 Tf 400 612 Td (NH2) Tj ET\nEMC\n")
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.add_blank_page(page_size=(595, 842))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(content.encode())
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    p = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page.obj, K=0))
    fig = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=doc, Pg=page.obj, K=1, Alt=String("A two-step scheme")))
    doc.K = Array([p, fig])
    root.K = doc
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([p, fig])])))
    root.ParentTreeNextKey = 1
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.Root.Lang = String("en-US")
    out = tmp_path / "coarse_scheme.pdf"
    pdf.save(out)
    tm = build_tagmap(out)
    f = next(e for e in tm["elements"] if e["role"] == "Figure")
    assert [x["mcid"] for x in tm["pieces"] if x["el"] == f["i"]] == [1]       # one piece for the whole scheme
    return out, f


def test_an_area_of_a_scheme_drawn_as_one_piece_becomes_a_figure_of_its_own(tmp_path):
    src, f = _coarse_scheme(tmp_path)
    # the right-hand structure and its label, in the tag map's units (top-left origin): PDF y 560..625 is 217..282
    box = [325, 210, 430, 290]
    dst, log = _edit(src, tmp_path, [{"op": "cut_area", "element": f["i"], "expect_role": "Figure", "page": 1, "bbox": box,
                                      "role": "Figure", "alt": "The product"}])
    assert "made into a new Figure" in log[0]
    after = build_tagmap(dst)
    figs = [e for e in after["elements"] if e["role"] == "Figure"]
    assert [e["alt"] for e in figs] == ["A two-step scheme", "The product"]          # the new one is read next
    left = [x for x in after["pieces"] if x["el"] == figs[0]["i"]]
    right = [x for x in after["pieces"] if x["el"] == figs[1]["i"]]
    assert right and all(x["bbox"][0] >= box[0] - 1 for x in right)
    assert left and all(x["bbox"][2] <= box[0] + 1 for x in left)                    # the arrow (middle at 270) stays
    assert any(x.get("text") == "NH2" for x in right) and any(x.get("text") == "OH" for x in left)


def test_cutting_an_area_refuses_boxes_with_none_or_all_of_it(tmp_path):
    src, f = _coarse_scheme(tmp_path)
    op = lambda box, **o: [{"op": "cut_area", "element": f["i"], "expect_role": "Figure", "page": 1, "bbox": box, "role": "Figure", "alt": "x", **o}]  # noqa: E731
    with pytest.raises(EditError, match="holds none"):
        apply_edits(src, tmp_path / "x.pdf", op([10, 10, 60, 60]))
    with pytest.raises(EditError, match="holds all"):
        apply_edits(src, tmp_path / "x.pdf", op([100, 150, 450, 320]))
    for bad, why in [({"bbox": [10, 10, 10, 60]}, "needs the page and a box"), ({"page": 0}, "needs the page"), ({"alt": ""}, "needs alt text")]:
        with pytest.raises(EditError, match=why):
            validate_ops([{**op([1, 1, 50, 50])[0], **bad}])


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


def test_moves_that_would_break_the_structure_are_refused(table, tmp_path):
    tm = build_tagmap(table)
    els = tm["elements"]
    rows = [e for e in els if e["role"] == "TR"]
    cells0 = [els[k] for k in rows[0]["kids"]]
    cells1 = [els[k] for k in rows[1]["kids"]]
    para = next(e for e in els if e["role"] == "P")
    bad = [
        # a cell into another row: the rows would be uneven
        ({"op": "move", "element": cells0[0]["i"], "expect_role": cells0[0]["role"], "target": cells1[0]["i"],
          "position": "before"}, "within its own row"),
        # a paragraph into a row (rows hold only cells)
        ({"op": "move", "element": para["i"], "expect_role": "P", "target": cells0[0]["i"], "position": "before"},
         "can only be"),
        # a row out of its table
        ({"op": "move", "element": rows[0]["i"], "expect_role": "TR", "target": para["i"], "position": "after"},
         "has to be inside"),
    ]
    for op, why in bad:
        with pytest.raises(EditError, match=why):
            apply_edits(table, tmp_path / "x.pdf", [op])
    # Reordering cells within their row, and rows within their table, is fine
    _edit(table, tmp_path, [
        {"op": "move", "element": cells0[1]["i"], "expect_role": cells0[1]["role"], "target": cells0[0]["i"],
         "position": "before"},
        {"op": "move", "element": rows[1]["i"], "expect_role": "TR", "target": rows[0]["i"], "position": "before"},
    ])


# --- areas ---------------------------------------------------------------------------------------

def _piece(tm, text):
    return next(p for p in tm["pieces"] if p.get("text", "").startswith(text))


def test_assign_moves_content_into_another_tag(report, tmp_path):
    tm = build_tagmap(report)
    heading, overview = _piece(tm, "Annual Accessibility"), _piece(tm, "Section 1")
    h = tm["elements"][heading["el"]]
    dst, log = _edit(report, tmp_path, [{"op": "assign", "element": h["i"], "expect_role": h["role"],
                                         "pieces": [{"page": 1, "mcid": overview["mcid"]}]}])
    after = build_tagmap(dst)
    moved = next(p for p in after["pieces"] if p["mcid"] == overview["mcid"] and p["page"] == 1)
    assert after["elements"][moved["el"]]["role"] == h["role"]
    assert len(after["elements"]) == len(tm["elements"]) - 1, "the emptied tag should be removed"
    assert "removed" in log[0]


def test_assign_across_pages_names_the_page(report, tmp_path):
    tm = build_tagmap(report)
    first = _piece(tm, "Annual Accessibility")
    other = next(p for p in tm["pieces"] if p["page"] == 2 and p["kind"] == "text")
    target = tm["elements"][first["el"]]
    dst, _ = _edit(report, tmp_path, [{"op": "assign", "element": target["i"], "expect_role": target["role"],
                                       "pieces": [{"page": 2, "mcid": other["mcid"]}]}])
    after = build_tagmap(dst)
    moved = next(p for p in after["pieces"] if p["page"] == 2 and p["mcid"] == other["mcid"])
    assert after["elements"][moved["el"]]["role"] == target["role"]


def test_assign_refuses_containers_and_untagged_content(report, tmp_path):
    tm = build_tagmap(report)
    doc = tm["elements"][0]
    piece = _piece(tm, "Section 1")
    with pytest.raises(EditError, match="holds other tags"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "assign", "element": doc["i"],
                                                  "pieces": [{"page": 1, "mcid": piece["mcid"]}]}])
    h = tm["elements"][piece["el"]]
    with pytest.raises(EditError, match="isn't tagged"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "assign", "element": h["i"],
                                                  "pieces": [{"page": 1, "mcid": 999}]}])
    with pytest.raises(EditError, match="pieces"):
        validate_ops([{"op": "assign", "element": 1, "pieces": []}])


def test_tag_artifacts_makes_a_new_tag_in_reading_order(report, tmp_path):
    tm = build_tagmap(report)
    banner = next(a for a in tm["artifacts"] if a["page"] == 1 and a.get("text") == "ACME Annual Report")
    title = tm["elements"][_piece(tm, "Annual Accessibility")["el"]]
    dst, log = _edit(report, tmp_path, [{"op": "tag_artifacts", "element": title["i"], "expect_role": title["role"],
                                         "position": "before", "page": 1, "sections": [banner["seq"]], "role": "P"}])
    after = build_tagmap(dst)
    new = next(p for p in after["pieces"] if p.get("text") == "ACME Annual Report")
    assert after["elements"][new["el"]]["role"] == "P"
    own = [e for e in after["elements"] if e["own"]]
    assert own[0]["i"] == new["el"], "the new tag should be read before the title"
    assert not any(a.get("text") == "ACME Annual Report" and a["page"] == 1 for a in after["artifacts"])
    assert "new P" in log[0]


def test_tag_artifacts_on_another_page_and_as_figure(table, tmp_path):
    tm = build_tagmap(table)
    cells = [a["seq"] for a in tm["artifacts"] if a["page"] == 1][:3]
    heading = tm["elements"][_piece(tm, "Quarterly")["el"]]
    dst, _ = _edit(table, tmp_path, [{"op": "tag_artifacts", "element": heading["i"], "expect_role": heading["role"],
                                      "position": "after", "page": 1, "sections": cells, "role": "Figure",
                                      "alt": "Header row background"}])
    after = build_tagmap(dst)
    figs = [e for e in after["elements"] if e["role"] == "Figure"]
    assert any(e.get("alt") == "Header row background" for e in figs)
    assert len(after["artifacts"]) == len(tm["artifacts"]) - 3


def test_tag_artifacts_refuses_bad_requests(report, table, tmp_path):
    tm = build_tagmap(report)
    title = tm["elements"][_piece(tm, "Annual Accessibility")["el"]]
    base = {"op": "tag_artifacts", "element": title["i"], "position": "after", "page": 1, "sections": [0]}
    with pytest.raises(EditError, match="alt text"):
        validate_ops([{**base, "role": "Figure"}])
    with pytest.raises(EditError, match="can be tagged as"):
        validate_ops([{**base, "role": "TD"}])
    with pytest.raises(EditError, match="no artifact section"):
        apply_edits(report, tmp_path / "x.pdf", [{**base, "role": "P", "sections": [40]}])
    tt = build_tagmap(table)
    td = next(e for e in tt["elements"] if e["role"] == "TD")
    with pytest.raises(EditError, match="can't go there"):
        apply_edits(table, tmp_path / "x.pdf", [{"op": "tag_artifacts", "element": td["i"], "position": "after",
                                                 "page": 1, "sections": [0], "role": "P"}])


def test_two_area_edits_on_one_page_use_the_tag_maps_numbers(table, tmp_path):
    tm = build_tagmap(table)
    arts = [a for a in tm["artifacts"] if a["page"] == 1]
    heading = tm["elements"][_piece(tm, "Quarterly")["el"]]
    op = {"op": "tag_artifacts", "element": heading["i"], "position": "after", "page": 1, "role": "Figure", "alt": "Cell"}
    dst, _ = _edit(table, tmp_path, [{**op, "sections": [arts[0]["seq"]]}, {**op, "sections": [arts[2]["seq"]]}])
    left = {tuple(a["bbox"]) for a in build_tagmap(dst)["artifacts"]}
    assert tuple(arts[0]["bbox"]) not in left and tuple(arts[2]["bbox"]) not in left
    assert tuple(arts[1]["bbox"]) in left
    with pytest.raises(EditError, match="already tagged"):
        apply_edits(table, tmp_path / "x.pdf", [{**op, "sections": [0]}, {**op, "sections": [0]}])


def test_tagmap_has_each_tags_own_text_in_full_for_reading_aloud(report):
    tm = build_tagmap(report)
    own = [e for e in tm["elements"] if e["own"] and e["role"] != "Figure"]
    assert own
    for e in own:
        words = e.get("say", e.get("text", "")) or e.get("name")  # a link or field with no text: its name
        assert words, f"element {e['i']} has nothing to read"
        if "say" in e:
            assert e["say"] != e.get("text")
    containers = [e for e in tm["elements"] if not e["own"]]
    assert all("say" not in e for e in containers)


# --- deleting tags -------------------------------------------------------------------------------

def _texts(tm):
    return [e.get("text") for e in sorted((e for e in tm["elements"] if e["own"]), key=lambda e: e["order"])]


def test_delete_a_tag_and_everything_in_it(report, tmp_path):
    tm = build_tagmap(report)
    lst = _first(tm, "L")
    inside = [e["i"] for e in tm["elements"] if e["i"] != lst["i"] and _is_under(tm, e, lst["i"])]
    gone = [e["text"] for e in tm["elements"] if e["i"] in inside and e["own"]]
    dst, log = _edit(report, tmp_path, [{"op": "delete", "element": lst["i"], "expect_role": "L", "children": "delete"}])
    after = build_tagmap(dst)
    assert len(after["elements"]) == len(tm["elements"]) - 1 - len(inside)
    # (the sample repeats its wording on page 2, so count: each of the list's lines is read once less)
    was, now = Counter(_texts(tm)), Counter(_texts(after))
    assert all(now[t] == was[t] - gone.count(t) for t in set(gone)), "its text should no longer be read"
    assert sum(was.values()) - sum(now.values()) == len(gone)
    assert len(after["artifacts"]) > len(tm["artifacts"])   # ...it's an artifact now, not untagged
    assert "deleted with" in log[0] and "artifacts" in log[0]


def _is_under(tm, e, ancestor):
    p = e["parent"]
    while p is not None:
        if p == ancestor:
            return True
        p = tm["elements"][p]["parent"]
    return False


def test_delete_only_the_tag_and_its_children_move_up(report, tmp_path):
    """A Sect is removed; what was in it stays, in the same order, one level up."""
    tm = build_tagmap(report)
    sect = next(e for e in tm["elements"] if e["role"] == "Sect" and e["kids"] and e["parent"] is not None)
    parent, kids = tm["elements"][sect["parent"]], [tm["elements"][k]["role"] for k in sect["kids"]]
    before = _texts(tm)
    dst, log = _edit(report, tmp_path, [{"op": "delete", "element": sect["i"], "expect_role": "Sect", "children": "lift"}])
    after = build_tagmap(dst)
    assert len(after["elements"]) == len(tm["elements"]) - 1 and _texts(after) == before
    new_parent = after["elements"][parent["i"]]
    at = parent["kids"].index(sect["i"])
    assert [after["elements"][k]["role"] for k in new_parent["kids"]][at:at + len(kids)] == kids
    assert "moved up a level" in log[0]


def test_removing_a_tag_refuses_what_would_break_the_tree(report, table, tmp_path):
    tm = build_tagmap(report)
    lst = _first(tm, "L")
    with pytest.raises(EditError, match="can't move up a level"):   # list items outside a list
        apply_edits(report, tmp_path / "x.pdf", [{"op": "delete", "element": lst["i"], "children": "lift"}])
    doc = tm["elements"][0]
    with pytest.raises(EditError, match="top tag"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "delete", "element": doc["i"], "children": "lift"}])
    with pytest.raises(EditError, match="only top-level"):
        apply_edits(report, tmp_path / "x.pdf", [{"op": "delete", "element": doc["i"], "children": "delete"}])
    tt = build_tagmap(table)
    tr = _first(tt, "TR")
    with pytest.raises(EditError, match="can't move up a level"):   # cells outside their row
        apply_edits(table, tmp_path / "x.pdf", [{"op": "delete", "element": tr["i"], "children": "lift"}])
    with pytest.raises(EditError, match="what happens"):
        validate_ops([{"op": "delete", "element": 1}])


def test_a_removed_tags_own_text_becomes_an_artifact_unless_its_parent_holds_text(report, tmp_path):
    tm = build_tagmap(report)
    p = next(e for e in tm["elements"] if e["role"] == "P" and e["own"] and not e["kids"]
             and tm["elements"][e["parent"]]["role"] in ("Sect", "Document"))
    dst, log = _edit(report, tmp_path, [{"op": "delete", "element": p["i"], "expect_role": "P", "children": "lift"}])
    after = build_tagmap(dst)
    assert Counter(_texts(after))[p["text"]] == Counter(_texts(tm))[p["text"]] - 1
    assert len(after["artifacts"]) == len(tm["artifacts"]) + 1
    assert "now artifacts" in log[0]
    # A tag inside a text tag (a Span in a paragraph): its text joins the paragraph, and is still read
    src2 = tmp_path / "nested.pdf"
    with pikepdf.open(report) as pdf:
        _, index = check_integrity(pdf)
        target = next(e for e in index.elements if e.role == "P" and e.content_refs and not e.kids)
        span = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Span, P=target.obj,
                                                    Pg=target.obj.get("/Pg"), K=target.obj.K))
        target.obj.K = pikepdf.Array([span])
        pdf.save(src2)
    tm2 = build_tagmap(src2)
    span = _first(tm2, "Span")
    dst2 = tmp_path / "unwrapped.pdf"
    log2 = apply_edits(src2, dst2, [{"op": "delete", "element": span["i"], "expect_role": "Span", "children": "lift"}])
    after2 = build_tagmap(dst2)
    assert not any(e["role"] == "Span" for e in after2["elements"]) and span["text"] in _texts(after2)
    assert "now part of the P" in log2[0] and _pixels(dst2) == _pixels(src2)


def _linked(tmp_path):
    """A heading, then a paragraph with two links in it, then another paragraph; tagged by the engine."""
    import pymupdf as fitz

    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document

    src = tmp_path / "links.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Where to find more", fontsize=18, fontname="hebo")
    page.insert_text((72, 140), "See the first site and the second site for details.", fontsize=11, fontname="helv")
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(106, 130, 150, 144), "uri": "https://example.org/one"})
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(176, 130, 238, 144), "uri": "https://example.org/two"})
    page.insert_text((72, 180), "A last paragraph with nothing special in it.", fontsize=11, fontname="helv")
    doc.save(src)
    tagged = tmp_path / "links_tagged.pdf"
    tag_document(src, tagged, prepare_analysis(src), title="Links", language="en-US")
    return tagged


def test_deleting_a_section_keeps_its_links_and_moves_them_up(tmp_path):
    src = _linked(tmp_path)
    tm = build_tagmap(src)
    links = [e for e in tm["elements"] if e["role"] == "Link"]
    assert len(links) == 2
    # The tag to delete: the links' common ancestor just under the top tag (a section, or their paragraph)
    target = links[0]
    while tm["elements"][target["parent"]]["parent"] is not None:
        target = tm["elements"][target["parent"]]
    assert target["role"] != "Link" and all(_is_under(tm, link, target["i"]) for link in links)
    dst, log = _edit(src, tmp_path, [{"op": "delete", "element": target["i"], "expect_role": target["role"],
                                      "children": "delete"}])
    after = build_tagmap(dst)
    kept = [e for e in after["elements"] if e["role"] == "Link"]
    assert len(kept) == 2 and "2 link(s) and form field(s) kept" in log[0]
    top = next(e for e in after["elements"] if e["parent"] is None)
    assert all(e["parent"] == top["i"] for e in kept)                    # moved up, in their place and order
    assert [e["name"] for e in kept] == [e["name"] for e in links]
    assert not [e for e in after["elements"] if e["role"] == target["role"] and e["i"] != top["i"]
                and (e.get("text") or "") == (target.get("text") or "")]


def test_a_link_itself_cant_be_deleted_with_what_is_in_it(tmp_path):
    src = _linked(tmp_path)
    link = _first(build_tagmap(src), "Link")
    with pytest.raises(EditError, match="link or form field itself"):
        apply_edits(src, tmp_path / "no.pdf", [{"op": "delete", "element": link["i"], "expect_role": "Link",
                                                "children": "delete"}])


def _glyphs(*runs):
    """Glyphs as pdfplumber gives them: (text, x0, top) each, 10 pt, 5 pt wide."""
    return [{"text": t, "x0": x, "x1": x + 5, "top": top, "size": 10.0} for t, x, top in runs]


def test_a_tags_text_has_a_space_where_a_line_ends():
    """#298: a paragraph's lines are drawn with no space between them; the tag's text gets one."""
    from app.verification.semantic import glyph_text

    text, starts = glyph_text(_glyphs(("t", 100, 100), ("o", 105, 100), ("d", 72, 114), ("o", 77, 114)))
    assert text == "to do" and starts == [0, 1, 3, 4]
    # A word broken with a hyphen at the line's end stays one word
    assert glyph_text(_glyphs(("a", 100, 100), ("-", 105, 100), ("b", 72, 114)))[0] == "a-b"
    # A raised or lowered script carries on to the right: no space
    assert glyph_text(_glyphs(("x", 100, 100), ("2", 105, 96)))[0] == "x2"
    # A word space drawn by moving the pen
    assert glyph_text(_glyphs(("a", 100, 100), ("b", 110, 100)))[0] == "a b"
    # Spaces that are drawn are collapsed, not doubled
    assert glyph_text(_glyphs(("a", 100, 100), (" ", 105, 100), ("b", 72, 114)))[0] == "a b"


def test_split_right_after_a_line_break(tmp_path):
    """One piece of text over several lines (no space drawn where they meet): a place in the tag's text, which has a
    space there, still finds the glyph that starts the next line."""
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_textbox(fitz.Rect(72, 90, 200, 140), "The guide says what to do when the lot is full.", fontsize=11,
                     fontname="tiro")
    second_line = p.get_text("dict")["blocks"][0]["lines"][1]["spans"][0]["text"].strip()
    src, tagged = tmp_path / "src.pdf", tmp_path / "tagged.pdf"
    doc.save(src)
    tag_document(src, tagged, prepare_analysis(src), title="T", language="en-US")
    para = next(e for e in build_tagmap(tagged)["elements"] if e["role"] == "P")
    assert para["text"] == "The guide says what to do when the lot is full." and not para.get("parts")
    char = para["text"].index(second_line)
    dst, _ = _edit(tagged, tmp_path, [{"op": "split", "element": para["i"], "expect_role": "P", "at": 0, "char": char}])
    assert [e.get("text") for e in build_tagmap(dst)["elements"] if e["role"] == "P"] == [
        para["text"][:char].strip(), para["text"][char:]]


def test_a_links_description_goes_on_its_annotation_and_cant_be_emptied(tmp_path):
    """#364: what a screen reader announces for a link is its annotation's /Contents."""
    src = _linked(tmp_path)
    link = _first(build_tagmap(src), "Link")
    dst, log = _edit(src, tmp_path, [{"op": "set_alt", "element": link["i"], "expect_role": "Link",
                                      "alt": "  The first site: example.org  "}])
    assert log == [f"element {link['i']} (Link): description set on 1 annotation(s)"]
    assert build_tagmap(dst)["elements"][link["i"]]["alt"] == "The first site: example.org"
    with pikepdf.open(dst) as pdf:
        contents = [str(a.Contents) for a in pdf.pages[0].Annots if a.get("/Subtype") == pikepdf.Name.Link]
    assert "The first site: example.org" in contents and len(contents) == 2
    with pytest.raises(EditError, match="needs a description"):
        apply_edits(src, tmp_path / "no.pdf", [{"op": "set_alt", "element": link["i"], "expect_role": "Link",
                                                "alt": " "}])


def test_any_tag_can_be_made_an_artifact_of_a_kind(report, tmp_path):
    """#364: a running header, a footer or a page number left tagged becomes page furniture; anything else, layout."""
    tm = build_tagmap(report)
    p = _first(tm, "P")
    dst, log = _edit(report, tmp_path, [{"op": "set_decorative", "element": p["i"], "expect_role": "P",
                                         "as": "footer"}])
    assert "marked a page footer" in log[0]
    with pikepdf.open(dst) as pdf:
        marks = [ins.operands[1] for page in pdf.pages for ins in pikepdf.parse_content_stream(page)
                 if str(ins.operator) == "BDC" and ins.operands[0] == pikepdf.Name.Artifact]
    assert any(m.get("/Type") == pikepdf.Name.Pagination and m.get("/Subtype") == pikepdf.Name.Footer for m in marks)
    assert len(build_tagmap(dst)["elements"]) == len(tm["elements"]) - 1  # the paragraph's tag is gone
    with pytest.raises(EditError, match="an artifact is one of"):
        validate_ops([{"op": "set_decorative", "element": p["i"], "expect_role": "P", "as": "watermark"}])


# --- the table editor (#370) ------------------------------------------------------------------------------------

def _grid_of(tm, table):
    """The table's rows of cells as (role, text, span), from the tag map."""
    rows = [tm["elements"][r] for r in tm["elements"][table]["kids"] if tm["elements"][r]["role"] == "TR"]
    return [[(tm["elements"][c]["role"], tm["elements"][c].get("text"), tm["elements"][c].get("span"))
             for c in r["kids"]] for r in rows]


def _as_is(tm, table):
    """The rebuild_table rows that leave the table as it is."""
    rows = [tm["elements"][r] for r in tm["elements"][table]["kids"] if tm["elements"][r]["role"] == "TR"]
    return [[{"from": [c], "role": tm["elements"][c]["role"],
              **({"scope": tm["elements"][c]["scope"]} if tm["elements"][c]["role"] == "TH" else {})}
             for c in r["kids"]] for r in rows]


def test_a_table_rebuilt_as_it_was_is_unchanged(table, tmp_path):
    tm = build_tagmap(table)
    t = _first(tm, "Table")["i"]
    dst, log = _edit(table, tmp_path, [{"op": "rebuild_table", "element": t, "expect_role": "Table",
                                        "rows": _as_is(tm, t)}])
    assert _grid_of(build_tagmap(dst), t) == _grid_of(tm, t) and "4 row(s) by 3 column(s)" in log[0]


def test_cells_merge_rows_go_into_their_neighbours_and_empty_ones_are_added(table, tmp_path):
    tm = build_tagmap(table)
    t = _first(tm, "Table")["i"]
    rows = _as_is(tm, t)
    q1, q1_docs = rows[1][0], rows[1][1]
    rows[1] = [{"from": q1["from"] + q1_docs["from"], "role": "TD", "cols": 2}, rows[1][2]]  # Q1 and 120: one cell
    q3 = rows.pop(3)                                                   # the Q3 row removed: into the row above
    for cell, gone in zip(rows[2], q3):
        cell["from"] += gone["from"]
    for row in rows:                                                   # a new empty column at the end
        row.append({"from": [], "role": "TH" if row is rows[0] else "TD", "scope": "Column"})
    dst, log = _edit(table, tmp_path, [{"op": "rebuild_table", "element": t, "expect_role": "Table", "rows": rows}])
    grid = _grid_of(build_tagmap(dst), t)
    assert [len(r) for r in grid] == [4, 3, 4]
    assert grid[1][0][2] == [1, 2] and "Q1" in grid[1][0][1] and "120" in grid[1][0][1]
    assert "Q2" in grid[2][0][1] and "Q3" in grid[2][0][1]          # nothing lost
    assert grid[0][3] == ("TH", None, None) and grid[2][3] == ("TD", None, None)
    assert "4 cell(s) merged into others" in log[0]


def test_two_header_rows_name_their_headers_and_a_caption_comes_and_goes(table, tmp_path):
    tm = build_tagmap(table)
    t = _first(tm, "Table")["i"]
    rows = _as_is(tm, t)
    rows[1] = [{**c, "role": "TH", "scope": "Column"} for c in rows[1]]  # a second header row: needs Headers
    note = next(e for e in tm["elements"] if e["role"] == "P" and "summarizes" in (e.get("text") or ""))
    dst, _ = _edit(table, tmp_path, [{"op": "rebuild_table", "element": t, "expect_role": "Table", "rows": rows,
                                      "caption": note["i"]}])
    after = build_tagmap(dst)
    t2 = _first(after, "Table")
    assert after["elements"][t2["kids"][0]]["role"] == "Caption"
    assert all(after["elements"][c].get("headers") for c in after["elements"][t2["kids"][3]]["kids"])
    # The caption taken away again: a paragraph just before the table
    dst2 = tmp_path / "nocap.pdf"
    apply_edits(dst, dst2, [{"op": "rebuild_table", "element": t2["i"], "expect_role": "Table",
                             "rows": _as_is(after, t2["i"]), "caption": None}])
    assert _errors(dst2) == []
    again = build_tagmap(dst2)
    t3 = _first(again, "Table")
    before = again["elements"][again["elements"][t3["i"]]["parent"]]["kids"]
    assert again["elements"][before[before.index(t3["i"]) - 1]]["role"] == "P"
    assert all(again["elements"][k]["role"] == "TR" for k in t3["kids"])


def test_a_table_edit_that_loses_content_or_doesnt_fit_is_refused(table, tmp_path):
    tm = build_tagmap(table)
    t = _first(tm, "Table")["i"]
    rows = _as_is(tm, t)
    with pytest.raises(EditError, match="aren't in the new table"):
        apply_edits(table, tmp_path / "x.pdf", [{"op": "rebuild_table", "element": t, "expect_role": "Table",
                                                 "rows": rows[:-1]}])
    short = _as_is(tm, t)
    short[2].append({"from": [], "role": "TD"})
    with pytest.raises(EditError, match="don't line up"):
        apply_edits(table, tmp_path / "x.pdf", [{"op": "rebuild_table", "element": t, "expect_role": "Table",
                                                 "rows": short}])
    with pytest.raises(EditError, match="one new cell only"):
        validate_ops([{"op": "rebuild_table", "element": t, "rows": [[{"from": [5], "role": "TD"},
                                                                     {"from": [5], "role": "TD"}]]}])


def test_paragraphs_become_a_table_from_their_pieces(tmp_path):
    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document

    src = tmp_path / "stacked.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for n, word in enumerate(["Lot", "Price", "North", "$145", "Library", "$120"]):  # one under the other
        page.insert_text((72, 120 + n * 30), word, fontsize=11, fontname="helv")
    doc.save(src)
    tagged = tmp_path / "stacked_tagged.pdf"
    tag_document(src, tagged, prepare_analysis(src), title="Stacked", language="en-US")
    tm = build_tagmap(tagged)
    tags = [e for e in tm["elements"] if e["role"] in ("P", "H1", "H2", "H3") and e.get("own")]
    pieces = [(e, p["k"]) for e in tags for p in (e.get("parts") or [{"k": 0, "kind": "content"}])
              if p["kind"] == "content"]
    assert len(pieces) == 6, [e.get("text") for e in tags]
    first = tags[0]
    ref = lambda e, k: {"at": k} if e["i"] == first["i"] else {"el": e["i"], "at": k}  # noqa: E731
    rows = [[{"parts": [ref(*pieces[r * 2 + c])], "role": "TH" if r == 0 else "TD", "scope": "Column"}
             for c in range(2)] for r in range(3)]
    op = {"op": "build_table", "element": first["i"], "expect_role": first["role"],
          "absorb": [e["i"] for e in tags[1:]], "rows": rows}
    dst, log = _edit(tagged, tmp_path, [op])
    after = build_tagmap(dst)
    t = _first(after, "Table")["i"]
    grid = _grid_of(after, t)
    assert [[c[0] for c in r] for r in grid] == [["TH", "TH"], ["TD", "TD"], ["TD", "TD"]]
    assert [[c[1] for c in r] for r in grid] == [["Lot", "Price"], ["North", "$145"], ["Library", "$120"]]
    assert "3 row(s) by 2 column(s)" in log[0]
    with pytest.raises(EditError, match="exactly one cell"):
        apply_edits(tagged, tmp_path / "x.pdf", [{**op, "rows": rows[:-1]}])


def test_a_figure_that_is_really_a_table_can_be_made_one(report, tmp_path):
    """A table drawn as a picture (a grid of ruled lines) was tagged as a figure: it can be made a table, and its alt
    text, which described a picture, goes."""
    tm = build_tagmap(report)
    fig = _first(tm, "Figure")
    assert fig.get("alt")
    pieces = [p["k"] for p in (fig.get("parts") or [{"k": 0, "kind": "content"}]) if p["kind"] == "content"]
    rows = [[{"parts": [{"at": k} for k in pieces], "role": "TH", "scope": "Column"}], [{"parts": [], "role": "TD"}]]
    dst, log = _edit(report, tmp_path, [{"op": "build_table", "element": fig["i"], "expect_role": "Figure",
                                         "rows": rows}])
    after = build_tagmap(dst)
    t = _first(after, "Table")
    assert [[c[0] for c in r] for r in _grid_of(after, t["i"])] == [["TH"], ["TD"]]
    figures = lambda m: sum(e["role"] == "Figure" for e in m["elements"])  # noqa: E731
    assert figures(after) == figures(tm) - 1
    assert "2 row(s) by 1 column(s)" in log[0]
