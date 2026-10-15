"""Complex tables: each cell names its headers (Headers on the cell, ID on each header), as Scope alone can't say
that a quarter's heading is under its year's. Simple tables keep Scope only."""
import pikepdf
import pytest
import pymupdf as fitz
from pikepdf import Name

from app.pipeline.models import TableCell, TableRegion
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.editor import EditError, apply_edits, validate_ops
from app.tagging.table_headers import header_links, is_complex
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap

#            Region | 2025 (2 columns) | 2026 (2 columns)
#  (Region)         | Q1   | Q2        | Q1   | Q2
#            North  | 10   | 12        | 14   | 16
#            South  | 11   | 13        | 15   | 17
GRID = [["Region", "2025", None, "2026", None],
        [None, "Q1", "Q2", "Q1", "Q2"],
        ["North", "10", "12", "14", "16"],
        ["South", "11", "13", "15", "17"]]
SPANS = {(0, 0): (2, 1), (0, 1): (1, 2), (0, 3): (1, 2)}
X0, Y0, W, H = 72, 120, 90, 24


def _cells(grid=GRID, spans=SPANS):
    out = []
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            if text is None:
                continue
            rs, cs = spans.get((r, c), (1, 1))
            out.append(TableCell(row=r, col=c, text=text, row_span=rs, col_span=cs,
                                 bbox=(X0 + W * c, Y0 + H * r, X0 + W * (c + cs), Y0 + H * (r + rs))))
    return out


def test_each_cell_is_read_with_the_headers_above_it_and_to_its_left():
    links = header_links(_cells(), header_rows=2, header_cols=1)
    assert links[(1, 1)] == [(0, 1)]                      # Q1 under 2025
    assert links[(1, 3)] == [(0, 3)]                      # Q1 under 2026
    assert links[(2, 4)] == [(0, 3), (1, 4), (2, 0)]     # 16: 2026, Q2, North
    assert links[(3, 1)] == [(0, 1), (1, 1), (3, 0)]     # 11: 2025, Q1, South
    assert links[(2, 0)] == [(0, 0)]                      # North under Region
    assert (0, 1) not in links and (0, 0) not in links    # a top heading has none; the corner heads no column heading


def test_which_tables_need_headers():
    simple = [["Name", "Age"], ["Ann", "34"]]
    assert not is_complex(_cells(simple, {}), header_rows=1, header_cols=0)
    assert not is_complex(_cells([["", "Q1"], ["North", "10"]], {}), header_rows=1, header_cols=1)  # two-way: Scope does
    assert is_complex(_cells(), header_rows=2, header_cols=1)
    grouped = [["Name", "Contact", None], ["Ann", "555 0100", "ann@example.org"]]
    assert is_complex(_cells(grouped, {(0, 1): (1, 2)}), header_rows=1, header_cols=0)       # spans some columns
    titled = [["Prices", None, None], ["Ann", "34", "Leeds"]]
    assert not is_complex(_cells(titled, {(0, 0): (1, 3)}), header_rows=1, header_cols=0)  # a title across it all
    deep = [["Item", "Size"], [None, "cm"], ["Desk", "120"]]
    assert is_complex(_cells(deep, {(0, 0): (2, 1)}), header_rows=1, header_cols=0)         # a header two rows deep


def _tagged(tmp_path, header_rows=2, header_cols=1, grid=GRID, spans=SPANS):
    """The table drawn on a page and tagged, with the headers and spans the AI's table analysis would give."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Sales by region", fontsize=16, fontname="hebo")
    for cell in _cells(grid, spans):
        page.insert_text((cell.bbox[0] + 4, cell.bbox[1] + 16), cell.text, fontsize=11, fontname="helv")
    src = tmp_path / "sales.pdf"
    doc.save(src)
    analysis = prepare_analysis(src)
    rows, cols = len(grid), len(grid[0])
    box = (X0, Y0, X0 + W * cols, Y0 + H * rows)
    # As the analysis does for a table it finds: the text inside is the table's, not paragraphs'
    pa = analysis.pages[0]
    pa.blocks = [b for b in pa.blocks if b.bbox[1] < Y0 - 2]
    pa.reading_order = [b.id for b in pa.blocks] + ["p0t0"]
    pa.tables = [TableRegion(id="p0t0", page=0, bbox=box, n_rows=rows, n_cols=cols, cells=_cells(grid, spans),
                             header_rows=header_rows, header_cols=header_cols)]
    out = tmp_path / "sales_tagged.pdf"
    return out, tag_document(src, out, analysis, title="Sales", language="en-US")


def test_a_complex_table_links_every_cell_to_its_headers(tmp_path):
    out, result = _tagged(tmp_path)
    assert result.tables == 1 and result.tables_linked == 1
    text = {e["i"]: e.get("text") for e in build_tagmap(out)["elements"]}
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        by_id = {bytes(e.obj.ID): e for e in index.elements if "/ID" in e.obj}
        assert by_id and all(e.role == "TH" for e in by_id.values())
        names = list(pdf.Root.StructTreeRoot.IDTree.Names)
        keys = [bytes(k) for k in names[::2]]
        assert keys == sorted(keys) and set(keys) == set(by_id)          # the IDTree has every ID, in order
        assert all(names[i + 1].objgen == by_id[bytes(names[i])].obj.objgen for i in range(0, len(names), 2))
        cells = [e for e in index.elements if e.role in ("TD", "TH")]

        def headers_of(words):
            found = [e for e in cells if text[e.index] == words]
            assert len(found) == 1, words
            attrs = found[0].obj.get("/A")
            return [text[by_id[bytes(h)].index] for h in attrs.Headers] if attrs is not None and "/Headers" in attrs else []

        assert headers_of("16") == ["2026", "Q2", "North"]
        assert headers_of("11") == ["2025", "Q1", "South"]
        assert headers_of("North") == ["Region"]
        assert headers_of("2025") == [] and headers_of("Region") == []
        # Scope stays, for readers that don't follow Headers
        year = next(e for e in cells if text[e.index] == "2025")
        assert year.obj.A.Scope == Name.Column


def test_a_simple_table_keeps_scope_only(tmp_path):
    grid = [["Name", "Age", "Town"], ["Ann", "34", "Leeds"], ["Bo", "29", "York"]]
    out, result = _tagged(tmp_path, header_rows=1, header_cols=0, grid=grid, spans={})
    assert result.tables == 1 and result.tables_linked == 0
    with pikepdf.open(out) as pdf:
        assert "/IDTree" not in pdf.Root.StructTreeRoot
        _, index = check_integrity(pdf)
        assert not any("/ID" in e.obj for e in index.elements)


def test_changing_a_cell_between_header_and_data_keeps_its_spans_and_headers(tmp_path):
    out, _ = _tagged(tmp_path)
    tm = build_tagmap(out)
    by_text = {e.get("text"): e for e in tm["elements"] if e["role"] in ("TD", "TH")}
    year, sixteen = by_text["2025"], by_text["16"]
    dst = tmp_path / "edited.pdf"
    apply_edits(out, dst, [{"op": "set_cell", "element": year["i"], "expect_role": "TH", "role": "TD"},
                           {"op": "set_cell", "element": sixteen["i"], "expect_role": "TD", "role": "TH", "scope": "Row"}])
    with pikepdf.open(dst) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        y, s = index.elements[year["i"]].obj, index.elements[sixteen["i"]].obj
        assert y.S == Name.TD and int(y.A.ColSpan) == 2 and "/Scope" not in y.A   # still two columns wide
        assert s.S == Name.TH and s.A.Scope == Name.Row and len(s.A.Headers) == 3  # still read with its headers


def _named(path):
    """Each cell's words -> the words of the headers it names, from the tag map."""
    tm = build_tagmap(path)
    text = {e["i"]: e.get("text") for e in tm["elements"]}
    return {e.get("text"): [text[h] for h in e.get("headers") or []] for e in tm["elements"] if e["role"] in ("TD", "TH")}, tm


def _ids_ok(path):
    """No integrity errors, every named ID is on a header cell of the file, and the IDTree has exactly the IDs."""
    with pikepdf.open(path) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        ids = {bytes(e.obj.ID): e for e in index.elements if "/ID" in e.obj}
        tree = pdf.Root.StructTreeRoot.get("/IDTree")
        assert ({bytes(k) for k in list(tree.Names)[::2]} if tree is not None else set()) == set(ids)
        for e in index.elements:
            attrs = e.obj.get("/A")
            if attrs is not None and not isinstance(attrs, pikepdf.Array) and "/Headers" in attrs:
                assert all(bytes(h) in ids and ids[bytes(h)].role == "TH" for h in attrs.Headers)


def test_the_tag_map_says_which_headers_each_cell_names(tmp_path):
    out, _ = _tagged(tmp_path)
    named, _ = _named(out)
    assert named["16"] == ["2026", "Q2", "North"] and named["North"] == ["Region"] and named["2025"] == []


def test_a_cells_headers_can_be_set_and_removed(tmp_path):
    out, _ = _tagged(tmp_path)
    named, tm = _named(out)
    by = {e.get("text"): e for e in tm["elements"] if e["role"] in ("TD", "TH")}
    dst = tmp_path / "set.pdf"
    log = apply_edits(out, dst, [
        {"op": "set_headers", "element": by["16"]["i"], "expect_role": "TD", "headers": [by["North"]["i"], by["2026"]["i"]]},
        {"op": "set_headers", "element": by["11"]["i"], "expect_role": "TD", "headers": []}])
    assert "read with header(s)" in log[0] and "headers removed" in log[1]
    after, _ = _named(dst)
    assert after["16"] == ["North", "2026"] and after["11"] == [] and after["12"] == named["12"]
    _ids_ok(dst)


def test_headers_can_be_named_in_a_table_that_had_none(tmp_path):
    grid = [["Name", "Age", "Town"], ["Ann", "34", "Leeds"], ["Bo", "29", "York"]]
    out, _ = _tagged(tmp_path, header_rows=1, header_cols=0, grid=grid, spans={})
    _, tm = _named(out)
    by = {e.get("text"): e for e in tm["elements"] if e["role"] in ("TD", "TH")}
    dst = tmp_path / "set.pdf"
    apply_edits(out, dst, [{"op": "set_headers", "element": by["34"]["i"], "expect_role": "TD", "headers": [by["Age"]["i"]]}])
    after, _ = _named(dst)
    assert after["34"] == ["Age"]
    _ids_ok(dst)   # the header got an ID, and the file an IDTree


def test_header_links_follow_a_change_between_header_and_data(tmp_path):
    out, _ = _tagged(tmp_path)
    _, tm = _named(out)
    by = {e.get("text"): e for e in tm["elements"] if e["role"] in ("TD", "TH")}
    q2_of_2026 = [e for e in tm["elements"] if e.get("text") == "Q2"][1]
    dst = tmp_path / "cells.pdf"
    log = apply_edits(out, dst, [
        # "Q2" under 2026 stops being a header: nobody names it any more
        {"op": "set_cell", "element": q2_of_2026["i"], "expect_role": "TH", "role": "TD"},
        # "South" made a data cell, then a header of its row again: the cells to its right name it once more
        {"op": "set_cell", "element": by["South"]["i"], "expect_role": "TH", "role": "TD"},
        {"op": "set_cell", "element": by["South"]["i"], "expect_role": "TH", "role": "TH", "scope": "Row"}])
    assert "follow" in log[0]
    after, _ = _named(dst)
    assert after["16"] == ["2026", "North"] and after["17"] == ["2026", "South"]
    assert after["11"] == ["2025", "Q1", "South"]
    _ids_ok(dst)


def test_headers_must_be_header_cells_of_the_same_table(tmp_path):
    out, _ = _tagged(tmp_path)
    _, tm = _named(out)
    by = {e.get("text"): e for e in tm["elements"] if e["role"] in ("TD", "TH")}
    heading = next(e for e in tm["elements"] if e["role"].startswith("H"))
    for heads, why in (([by["12"]["i"]], "isn't a header cell"), ([heading["i"]], "isn't a header cell")):
        with pytest.raises(EditError, match=why):
            apply_edits(out, tmp_path / "x.pdf", [{"op": "set_headers", "element": by["16"]["i"], "expect_role": "TD", "headers": heads}])
    with pytest.raises(EditError, match="only a table cell"):
        apply_edits(out, tmp_path / "x.pdf", [{"op": "set_headers", "element": heading["i"], "expect_role": heading["role"], "headers": []}])
    for bad in ({"op": "set_headers", "element": 3, "headers": [3]}, {"op": "set_headers", "element": 3, "headers": "4"},
                {"op": "set_headers", "element": 3, "headers": [4, 4]}):
        with pytest.raises(EditError, match="headers are a list"):
            validate_ops([bad])
