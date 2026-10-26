"""Kept tags: content claimed by two of the file's own tags (a second set of tags left over the same stretch of a
page, 490 times in a 332-page clinical guideline) is given to one of them, so the job no longer fails."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap
from tests.engine.pdfs import integrity_errors


def _tagged_twice(path, parent_tree_names="first"):
    """Three lines of text. The first set of tags (a heading and two paragraphs) covers all three; a second set
    (two paragraphs) claims the last two again."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    lines = ["Dosing", "Give the first dose in the morning.", "Check the level after five days."]
    content = [f"/P <</MCID {i}>> BDC BT /F1 12 Tf 72 {700 - 30 * i} Td ({t}) Tj ET EMC".encode()
               for i, t in enumerate(lines)]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))

    def elem(role, mcid):
        return pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(role), P=doc, Pg=page, K=mcid))
    first = [elem("/H1", 0), elem("/P", 1), elem("/P", 2)]
    second = [elem("/P", 1), elem("/P", 2)]
    doc.K = Array(first + second)
    root.K = Array([doc])
    named = first if parent_tree_names == "first" else [first[0]] + second
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(named)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)
    return [e.objgen for e in first], [e.objgen for e in second]


def _run(tmp_path, **kw):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _tagged_twice(src, **kw)
    result = tag_document(src, out, prepare_analysis(src), title="Dosing", language="en-US", pdfua_part=1)
    return out, result


def test_content_claimed_twice_is_read_once(tmp_path):
    out, result = _run(tmp_path)
    assert integrity_errors(out) == []
    texts = [e["text"] for e in build_tagmap(out)["elements"] if e.get("own") and (e.get("text") or "").strip()]
    assert texts == ["Dosing", "Give the first dose in the morning.", "Check the level after five days."]
    assert any("claimed by more than one" in f and "page(s) 1" in f for f in result.existing_tags["findings"])


def test_the_parent_tree_decides_which_tag_keeps_it(tmp_path):
    src = tmp_path / "in.pdf"
    first, second = _tagged_twice(src, parent_tree_names="second")
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="Dosing", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        # The second set, which the parent tree names, still holds the two lines; the first set's copies are gone
        assert {int(pdf.get_object(og).K[0]) if isinstance(pdf.get_object(og).K, Array) else int(pdf.get_object(og).K)
                for og in second} == {1, 2}
    texts = [e["text"] for e in build_tagmap(out)["elements"] if e.get("own") and (e.get("text") or "").strip()]
    assert sorted(texts) == sorted(["Dosing", "Give the first dose in the morning.", "Check the level after five days."])


def test_a_link_attached_straight_to_a_table_cell_gets_a_link_tag(tmp_path):
    """53 entries of the same guideline's index: the annotation sat in the TD, beside the entry's words."""
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    content = [b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (Index) Tj ET EMC",
               b"/TD <</MCID 1>> BDC BT /F1 12 Tf 72 660 Td (Asthma) Tj ET EMC",
               b"/TD <</MCID 2>> BDC BT /F1 12 Tf 300 660 Td (page 12) Tj ET EMC"]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([70, 655, 130, 675]),
                                         Border=Array([0, 0, 0]), StructParent=1,
                                         A=Dictionary(S=Name.URI, URI=pikepdf.String("https://example.org/asthma"))))
    page.Annots = Array([annot])
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))

    def elem(role, parent, k):
        return pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(role), P=parent, Pg=page, K=k))
    heading = elem("/H1", doc, 0)
    table = elem("/Table", doc, None)
    row = elem("/TR", table, None)
    first = elem("/TD", row, None)
    first.K = Array([1, Dictionary(Type=Name.OBJR, Obj=annot)])
    second = elem("/TD", row, 2)
    row.K, table.K = Array([first, second]), Array([row])
    doc.K, root.K = Array([heading, table]), Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([heading, first, second]), 1, first])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(src)

    tag_document(src, out, prepare_analysis(src), title="Index", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as done:
        findings, _ = check_integrity(done)
        assert [f for f in findings if f.severity == "error"] == []
    by_i = {e["i"]: e for e in build_tagmap(out)["elements"]}
    links = [e for e in by_i.values() if e["role"] == "Link"]
    assert len(links) == 1 and by_i[links[0]["parent"]]["role"] == "TD"
    assert "Asthma" in (links[0].get("text") or "")


def _table_tagged_twice(path, blank_cell=False):
    """A heading and a 2×2 table, the table tagged twice over the same four cells (#556). With `blank_cell`, one cell
    of the table holds nothing in the file either (a blank in the grid)."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    cells = [("Fruit", 72, 660), ("Colour", 300, 660), ("Lemon", 72, 630), ("Yellow", 300, 630)]
    content = [b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (Fruit colours) Tj ET EMC"]
    content += [f"/TD <</MCID {i + 1}>> BDC BT /F1 12 Tf {x} {y} Td ({t}) Tj ET EMC".encode()
                for i, (t, x, y) in enumerate(cells) if not (blank_cell and i == 3)]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))

    def elem(role, parent, k=None):
        return pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(role), P=parent, Pg=page,
                                            K=k if k is not None else Array()))

    def table():
        t = elem("/Table", doc)
        rows = [elem("/TR", t), elem("/TR", t)]
        tds = []
        for n, row in enumerate(rows):
            row_cells = [elem("/TD", row, Array() if blank_cell and 2 * n + c == 3 else 2 * n + c + 1)
                         for c in range(2)]
            row.K = Array(row_cells)
            tds += row_cells
        t.K = Array(rows)
        return t, tds
    heading = elem("/H1", doc, 0)
    first, first_cells = table()
    second, second_cells = table()
    doc.K, root.K = Array([heading, first, second]), Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([heading] + first_cells)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def _tables(out):
    elements = build_tagmap(out)["elements"]
    tables = [e for e in elements if e["role"] == "Table"]
    return elements, tables


def test_the_emptied_copy_of_a_table_tagged_twice_goes_whole(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _table_tagged_twice(src)
    tag_document(src, out, prepare_analysis(src), title="Fruit", language="en-US", pdfua_part=1)
    assert integrity_errors(out) == []
    elements, tables = _tables(out)
    assert len(tables) == 1
    assert [e["role"] for e in elements].count("TR") == 2 and [e["role"] for e in elements].count("TD") == 4
    assert "Lemon" in tables[0]["text"]


def test_a_blank_cell_in_a_table_with_content_stays(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _table_tagged_twice(src, blank_cell=True)
    tag_document(src, out, prepare_analysis(src), title="Fruit", language="en-US", pdfua_part=1)
    elements, tables = _tables(out)
    assert len(tables) == 1
    assert [e["role"] for e in elements].count("TD") == 4  # the blank keeps the grid
