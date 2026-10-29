"""Tags a save makes, named by its later edits before they exist (#692): an edit that makes tags carries an id, and a
later edit names one as {"made": id, "group": k}. Each edit keeps the tag tree valid and the pages as they were."""
import pytest

from app.tagging.editor import EditError, apply_edits, validate_ops
from app.verification.tagmap import build_tagmap
from tests.engine.test_editor import _edit, _piece, _tag


@pytest.fixture(scope="module")
def report(samples, tmp_path_factory):
    return _tag(samples, tmp_path_factory, "simple_report")


def made(id_, group=0):
    return {"made": id_, "group": group}


def _banner(tm):
    return next(a for a in tm["artifacts"] if a["page"] == 1 and a.get("text") == "ACME Annual Report")


def _area(tm):
    """The AI's tags for an area of page 1, as the tag manager sends them: the list's items as one paragraph, the figure
    with its caption, and the banner (untagged) as a paragraph."""
    on1 = {p["mcid"]: p for p in tm["pieces"] if p["page"] == 1}
    fig = next(m for m, p in on1.items() if p["kind"] == "image")
    caption = next(m for m, p in on1.items() if (p.get("text") or "").startswith("Figure 1"))
    items = [m for m, p in on1.items() if tm["elements"][p["el"]]["role"] == "LBody"]
    anchor = tm["elements"][on1[items[0]]["el"]]
    return {"op": "regroup", "id": "area1", "element": anchor["i"], "expect_role": "LBody", "position": "before",
            "page": 1, "groups": [{"role": "P", "pieces": items},
                                  {"role": "Figure", "pieces": [fig, caption], "alt": "Bar chart"},
                                  {"role": "P", "sections": [_banner(tm)["seq"]]}]}


def _holder(tm, text):
    return tm["elements"][_piece(tm, text)["el"]]


def test_an_areas_new_tags_are_changed_in_the_same_save(report, tmp_path):
    tm = build_tagmap(report)
    dst, log = _edit(report, tmp_path, [
        _area(tm),
        {"op": "set_role", "element": made("area1", 0), "expect_role": "P", "role": "H2"},
        {"op": "set_alt", "element": made("area1", 1), "expect_role": "Figure", "alt": "Progress by quarter"},
        {"op": "set_lang", "element": made("area1", 2), "expect_role": "P", "lang": "fr-CA"}])
    assert len(log) == 4
    after = build_tagmap(dst)
    assert _holder(after, "• Headings a")["role"] == "H2"
    assert any(e["role"] == "Figure" and e["alt"] == "Progress by quarter" for e in after["elements"])
    assert _holder(after, "ACME Annual")["lang"] == "fr-CA"


def test_a_tag_is_moved_next_to_and_into_a_new_tag_and_a_new_tag_moved(report, tmp_path):
    tm = build_tagmap(report)
    els = tm["elements"]
    title = _holder(tm, "Annual Accessibility")
    sect = next(e for e in els if e["role"] == "Sect" and e["parent"] is not None)
    dst, _ = _edit(report, tmp_path, [
        _area(tm),
        # The title read right after the new banner paragraph, then the new figure nested into a section
        {"op": "move", "element": title["i"], "expect_role": title["role"], "target": made("area1", 2),
         "position": "after"},
        {"op": "move", "element": made("area1", 1), "expect_role": "Figure", "target": sect["i"], "position": "in"}])
    after = build_tagmap(dst)
    ael = after["elements"]
    banner, moved = _holder(after, "ACME Annual"), _holder(after, "Annual Accessibility")
    kids = ael[banner["parent"]]["kids"]
    assert moved["parent"] == banner["parent"] and kids.index(moved["i"]) == kids.index(banner["i"]) + 1
    fig = next(e for e in ael if e["role"] == "Figure" and e["alt"] == "Bar chart")
    assert ael[fig["parent"]]["role"] == "Sect" and ael[fig["parent"]]["kids"][-1] == fig["i"]


def test_a_new_tag_is_merged_and_deleted_before_the_save(report, tmp_path):
    tm = build_tagmap(report)
    title = _holder(tm, "Annual Accessibility")
    dst, _ = _edit(report, tmp_path, [
        {"op": "tag_artifacts", "id": "t1", "element": title["i"], "expect_role": title["role"], "position": "before",
         "page": 1, "sections": [_banner(tm)["seq"]], "role": "P"},
        {"op": "merge", "element": made("t1"), "expect_role": "P", "absorb": [title["i"]]}])
    after = build_tagmap(dst)
    assert _holder(after, "ACME Annual")["i"] == _holder(after, "Annual Accessibility")["i"]
    dst2, _ = _edit(report, tmp_path / "..", [
        {"op": "tag_artifacts", "id": "t1", "element": title["i"], "expect_role": title["role"], "position": "before",
         "page": 1, "sections": [_banner(tm)["seq"]], "role": "P"},
        {"op": "delete", "element": made("t1"), "expect_role": "P", "children": "delete"}])
    assert any(a.get("text") == "ACME Annual Report" for a in build_tagmap(dst2)["artifacts"])


def test_the_new_part_of_a_split_is_named_and_later_edits_by_number_still_find_their_tags(report, tmp_path):
    tm = build_tagmap(report)
    els = tm["elements"]
    target = next(e for e in els if e.get("parts") and len(e["parts"]) >= 2 and e["role"] == "P")
    later = next(e for e in els if e["role"] == "P" and e["i"] > target["i"] + 2 and e.get("text"))
    dst, _ = _edit(report, tmp_path, [
        {"op": "split", "id": "s", "element": target["i"], "expect_role": "P", "at": 1},
        {"op": "set_role", "element": made("s"), "expect_role": "P", "role": "H3"},
        # Numbered after the split moved every later tag's number by one: still the same tag
        {"op": "set_role", "element": later["i"], "expect_role": "P", "role": "H4"}])
    after = build_tagmap(dst)["elements"]
    kept = after[target["i"]]
    part = next(e for e in after if e["parent"] == kept["parent"] and e["i"] > kept["i"])
    assert part["role"] == "H3"
    now = after[later["i"] + 1]  # one tag more before it
    assert (now["role"], now["text"]) == ("H4", later["text"])
    assert sum(e["role"] == "H4" for e in after) == 1


def test_a_figure_taken_out_of_a_figure_is_given_its_alt_text_later(tmp_path):
    from tests.engine.test_editor import _figure_of_four
    src, f, pieces = _figure_of_four(tmp_path)
    take = [{"page": x["page"], "mcid": x["mcid"]} for x in pieces[2:]]
    dst, _ = _edit(src, tmp_path, [
        {"op": "take_pieces", "id": "f2", "element": f["i"], "expect_role": "Figure", "pieces": take, "role": "Figure",
         "alt": "Steps"},
        {"op": "set_alt", "element": made("f2"), "expect_role": "Figure", "alt": "The last two steps"}])
    alts = [e["alt"] for e in build_tagmap(dst)["elements"] if e["role"] == "Figure"]
    assert alts == ["Four steps of a method", "The last two steps"]


def test_names_that_cant_work_are_refused(report, tmp_path):
    tm = build_tagmap(report)
    area = _area(tm)
    alt = lambda ref: {"op": "set_alt", "element": ref, "expect_role": "Figure", "alt": "x"}  # noqa: E731
    with pytest.raises(EditError, match="no earlier change"):
        validate_ops([alt(made("area1", 1)), area])     # named before it's made
    with pytest.raises(EditError, match="no earlier change"):
        validate_ops([area, alt(made("area1", 3))])     # it makes three
    with pytest.raises(EditError, match="no earlier change"):
        validate_ops([area, alt({"made": "area1"})])
    with pytest.raises(EditError, match="only an edit that makes a tag"):
        validate_ops([{**alt(1), "id": "x"}])
    with pytest.raises(EditError, match="same name"):
        validate_ops([area, {**area, "groups": area["groups"][:1]}])
    # Named where a number goes, the shape checks still apply: a tag can't take itself in
    with pytest.raises(EditError):
        validate_ops([area, {"op": "merge", "element": made("area1"), "absorb": [made("area1")]}])
    with pytest.raises(EditError, match="removed by an earlier change"):
        apply_edits(report, tmp_path / "x.pdf", [
            area, {"op": "delete", "element": made("area1", 1), "expect_role": "Figure", "children": "delete"},
            alt(made("area1", 1))])
    # A number is still checked against the file before anything runs
    with pytest.raises(EditError, match="not a Table"):
        apply_edits(report, tmp_path / "x.pdf", [area, {"op": "set_lang", "element": 1, "expect_role": "Table",
                                                         "lang": "fr"}])


# --- lists and tables built in the save (phase 3): their tags named in the order the tag manager shows them ----------

def test_a_list_built_from_lines_has_its_heading_list_and_items_named(tmp_path):
    from tests.engine.test_editor import NOTES, _one_paragraph_of_notes
    src, p = _one_paragraph_of_notes(tmp_path)
    rows = [{"at": 0, "as": "H3"}, {"at": 1, "level": 1}, {"at": 2, "level": 2}, {"at": 3, "level": 1}]
    # Shown as: H3, L, LI, L (nested), LI, LI
    dst, _ = _edit(src, tmp_path, [
        {"op": "build_list", "id": "b", "element": p["i"], "expect_role": "P", "rows": rows},
        {"op": "set_role", "element": made("b", 0), "expect_role": "H3", "role": "H2"},
        {"op": "set_lang", "element": made("b", 1), "expect_role": "L", "lang": "fr-CA"},
        {"op": "set_lang", "element": made("b", 4), "expect_role": "LI", "lang": "de"}])
    els = build_tagmap(dst)["elements"]
    assert next(e for e in els if e["role"] == "H2")["text"].startswith(NOTES[0][:20])
    lists = [e for e in els if e["role"] == "L"]
    assert lists[0]["lang"] == "fr-CA" and lists[1].get("lang") != "fr-CA"
    nested_item = next(e for e in els if e["role"] == "LI" and e.get("lang") == "de")
    assert NOTES[2][:20] in nested_item["text"]


def test_a_list_made_of_tags_is_named(tmp_path):
    from tests.engine.test_editor import _notes, _notes_doc
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    dst, _ = _edit(src, tmp_path, [
        {"op": "make_list", "id": "m", "element": a["i"], "expect_role": "P", "absorb": [b["i"]]},
        {"op": "set_lang", "element": made("m"), "expect_role": "L", "lang": "es"}])
    assert next(e for e in build_tagmap(dst)["elements"] if e["role"] == "L")["lang"] == "es"


def test_a_table_built_and_a_table_rebuilt_have_their_cells_named(tmp_path):
    from tests.engine.test_editor import _first, _notes, _notes_doc, _tag
    src = _notes_doc(tmp_path)
    a, b, c, d = _notes(build_tagmap(src))
    cell = lambda el, role="TD": {"parts": [{"el": el, "at": 0}], "role": role}  # noqa: E731
    # Shown as: Table, TR, cell, cell, TR, cell, cell
    dst, _ = _edit(src, tmp_path, [
        {"op": "build_table", "id": "t", "element": a["i"], "expect_role": "P", "absorb": [b["i"], c["i"], d["i"]],
         "rows": [[cell(a["i"]), cell(b["i"])], [cell(c["i"]), cell(d["i"])]]},
        {"op": "set_cell", "element": made("t", 2), "expect_role": "TD", "role": "TH", "scope": "Column"},
        {"op": "set_lang", "element": made("t", 6), "expect_role": "TD", "lang": "fr"}])
    els = build_tagmap(dst)["elements"]
    t = _first({"elements": els}, "Table")
    rows = [[els[k] for k in els[r]["kids"]] for r in t["kids"]]
    assert rows[0][0]["role"] == "TH" and rows[1][1]["lang"] == "fr"


def test_a_rebuilt_tables_cells_are_named(samples, tmp_path_factory, tmp_path):
    from tests.engine.test_editor import _first, _tag
    table = _tag(samples, tmp_path_factory, "table_doc")
    tm = build_tagmap(table)
    t = _first(tm, "Table")
    old = [tm["elements"][r]["kids"] for r in t["kids"]]
    same = [[{"from": [k], "role": tm["elements"][k]["role"]} for k in row] for row in old]
    # Shown as: Table, TR, its cells, TR, ...: the first row's last cell is group 1 + 1 + len(row)
    last = 2 + len(old[0]) - 1
    dst, _ = _edit(table, tmp_path, [
        {"op": "rebuild_table", "id": "r", "element": t["i"], "expect_role": "Table", "rows": same},
        {"op": "set_lang", "element": made("r", last), "expect_role": "TH", "lang": "fr"}])
    after = build_tagmap(dst)
    t2 = _first(after, "Table")
    first_row = after["elements"][t2["kids"][0]]["kids"]
    assert after["elements"][first_row[-1]]["lang"] == "fr"


# --- content of a tag deleted earlier in the save, tagged again (phase 4) -------------------------------------------

def test_a_deleted_tags_content_is_moved_into_another_tag_in_the_same_save(report, tmp_path):
    tm = build_tagmap(report)
    title = _holder(tm, "Annual Accessibility")
    gone = _holder(tm, "Accessible d")  # a paragraph, deleted with its content
    piece = next(p for p in tm["pieces"] if p["el"] == gone["i"])
    dst, log = _edit(report, tmp_path, [
        {"op": "delete", "element": gone["i"], "expect_role": "P", "children": "delete"},
        {"op": "assign", "element": title["i"], "expect_role": title["role"],
         "pieces": [{"page": piece["page"], "mcid": piece["mcid"]}]}])
    after = build_tagmap(dst)
    held = next(p for p in after["pieces"] if p["page"] == piece["page"] and p["mcid"] == piece["mcid"])
    assert after["elements"][held["el"]]["text"].startswith("Annual Accessibility")
    assert "1 more piece(s)" in log[1]
    # ...and into a new tag, from an area, the same way (regroup)
    dst2, _ = _edit(report, tmp_path / "..", [
        {"op": "delete", "element": gone["i"], "expect_role": "P", "children": "delete"},
        {"op": "regroup", "element": title["i"], "expect_role": title["role"], "position": "after", "page": piece["page"],
         "groups": [{"role": "Caption", "pieces": [piece["mcid"]]}]}])
    after2 = build_tagmap(dst2)
    held2 = next(p for p in after2["pieces"] if p["page"] == piece["page"] and p["mcid"] == piece["mcid"])
    assert after2["elements"][held2["el"]]["role"] == "Caption"
