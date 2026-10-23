"""Adobe's Autotag leaves out what PDF/UA asks of the document: its files have no PDF/UA identifier (5-1), keep a
title of "Microsoft Word - x.docx", and give TH cells no Scope (7.5-1). The improved copy adds them; a header cell
whose row doesn't say what it heads is left as Adobe tagged it."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.adobe_job import _describe, _document_fixes
from app.pipeline.orchestrator import JobOptions


def _table(pdf, rows: list[str], headers: bool = False):
    """A Table of rows written as "HDD" (H: TH, D: TD), the first under THead when there are several."""
    table = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Table))
    trs = []
    for n, row in enumerate(rows):
        tr = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR))
        cells = []
        for c in row:
            cell = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/TH" if c == "H" else "/TD"), P=tr,
                                                A=Array([Dictionary(O=Name.Table, ADBE_ColIndex=len(cells))])))
            if headers and c == "D":
                cell.Headers = Array([String("h0")])
            cells.append(cell)
        tr.K = Array(cells)
        trs.append(tr)
    table.K = Array(trs)
    return table


def _doc(path, title: str, tables: list) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    h1 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, P=doc))
    built = [_table(pdf, rows, headers) for rows, headers in tables]
    for t in built:
        t.P = doc
    doc.K = Array([h1] + built)
    root.K = Array([doc])
    pdf.Root.StructTreeRoot = root
    pdf.docinfo[Name.Title] = String(title)
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:title"] = title
    pdf.save(path)


def _scopes(path) -> list[list[str | None]]:
    with pikepdf.open(path) as pdf:
        out = []
        for table in [o for o in pdf.objects if isinstance(o, Dictionary) and o.get("/S") == Name.Table]:
            for tr in table.K:
                row = []
                for c in tr.K:
                    scope = next((str(a.Scope)[1:] for a in c.A if "/Scope" in a), None)
                    row.append(scope if c.S == Name.TH else "-")
                out.append(row)
        return out


TAGMAP = {"elements": [{"i": 1, "role": "H1", "text": "Introduction to Physics\nCourse Syllabus"}]}


def test_the_improved_copy_gets_identifier_title_and_scope(tmp_path):
    path = tmp_path / "adobe.pdf"
    _doc(path, "Microsoft Word - syllabus_accessible.docx", [(["HHH", "HDD", "HDD"], False)])
    done = _document_fixes(path, TAGMAP, JobOptions(stem="syllabus"))
    assert done == {"title": "Introduction to Physics Course Syllabus", "pdfua_identifier": True, "header_scope": 5,
                    "self_links": 0, "dates": 0}
    with pikepdf.open(path) as pdf:
        assert str(pdf.docinfo.Title) == "Introduction to Physics Course Syllabus"
        assert pdf.Root.ViewerPreferences.DisplayDocTitle is True
        with pdf.open_metadata() as meta:
            assert meta["pdfuaid:part"] == "1" and meta["dc:title"] == "Introduction to Physics Course Syllabus"
    assert _scopes(path) == [["Column"] * 3, ["Row", "-", "-"], ["Row", "-", "-"]]
    # Done once: a second pass changes nothing
    assert _document_fixes(path, TAGMAP, JobOptions(stem="syllabus")) == {
        "title": None, "pdfua_identifier": False, "header_scope": 0, "self_links": 0, "dates": 0}
    assert "PDF/UA identifier added" in _describe(done) and "Scope given to 5" in _describe(done)


def test_a_good_title_stays_and_unclear_headers_are_left(tmp_path):
    path = tmp_path / "adobe.pdf"
    # a header cell in the middle of a row of data, and a table whose cells name their headers
    _doc(path, "Physics 101 Syllabus", [(["DHD"], False), (["HHH", "HDD"], True)])
    done = _document_fixes(path, TAGMAP, JobOptions(stem="syllabus"))
    assert done["title"] is None and done["header_scope"] == 0
    with pikepdf.open(path) as pdf:
        assert str(pdf.docinfo.Title) == "Physics 101 Syllabus"
    assert _scopes(path) == [["-", None, "-"], [None] * 3, [None, "-", "-"]]


def test_no_heading_falls_back_to_the_file_name(tmp_path):
    path = tmp_path / "adobe.pdf"
    _doc(path, "Microsoft PowerPoint - week3.pptx", [])
    assert _document_fixes(path, {"elements": []}, JobOptions(stem="week_3_slides"))["title"] == "week 3 slides"
