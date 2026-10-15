"""Marked-content ids and annotation flags that aren't integers. A PDF can write anything there; the tagger, the
builder and the checkers turned each into an integer and a crafted file failed the job with a traceback. An id
that isn't an integer names nothing: the key is dropped from the page, the reference is removed from the tree,
and the content is tagged afresh."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _font(pdf):
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))


def _page(pdf, content: bytes, font):
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                                        Resources=Dictionary(Font=Dictionary(F1=font)), Contents=pdf.make_stream(content)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    return page


def _untagged_with_odd_id(path):
    """No tree; one section whose id is a name, one whose id is a string."""
    pdf = pikepdf.new()
    _page(pdf, b"/P <</MCID /x>> BDC BT /F1 12 Tf 72 700 Td (First line) Tj ET EMC\n"
               b"/P <</MCID (7)>> BDC BT /F1 12 Tf 72 670 Td (Second line) Tj ET EMC", _font(pdf))
    pdf.save(path)


def _kept_tree_with_odd_reference(path):
    """A kept tree: one element with a good reference, one with a reference whose id is a string."""
    pdf = pikepdf.new()
    page = _page(pdf, b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (Good) Tj ET EMC\n"
                      b"/P <</MCID 1>> BDC BT /F1 12 Tf 72 670 Td (Also good) Tj ET EMC", _font(pdf))
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    good = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page, K=0))
    odd = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page,
                                       K=Array([1, Dictionary(Type=Name.MCR, Pg=page, MCID="a")])))
    doc.K = Array([good, odd])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([good, odd])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def _link_with_odd_flags(path):
    pdf = pikepdf.new()
    page = _page(pdf, b"BT /F1 12 Tf 72 700 Td (A link) Tj ET", _font(pdf))
    link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([72, 690, 150, 712]),
                                        F=Name("/Hidden"), A=Dictionary(S=Name.URI, URI="https://example.org")))
    page.Annots = Array([link])
    pdf.save(path)


def _tag(tmp_path, make):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    make(src)
    result = tag_document(src, out, prepare_analysis(src), title="Odd", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
    assert [f for f in findings if f.severity == "error"] == [], findings
    return result, index


def test_a_page_section_whose_id_is_not_an_integer_is_tagged_afresh(tmp_path):
    _, index = _tag(tmp_path, _untagged_with_odd_id)
    texts = [e["text"] for e in build_tagmap(tmp_path / "out.pdf")["elements"] if e.get("own")]
    assert sorted(t for t in texts if t) == ["First line", "Second line"]


def test_a_kept_tree_reference_whose_id_is_not_an_integer_is_removed(tmp_path):
    result, index = _tag(tmp_path, _kept_tree_with_odd_reference)
    assert not result.existing_tags["rebuilt_tree"] and result.existing_tags["repaired"] >= 1
    assert sum(1 for e in index.elements if e.role == "P") == 2


def test_an_annotation_whose_flags_are_not_an_integer_is_still_tagged(tmp_path):
    result, index = _tag(tmp_path, _link_with_odd_flags)
    assert result.links == 1


def test_the_checker_reports_an_odd_id_instead_of_raising(tmp_path):
    _kept_tree_with_odd_reference(tmp_path / "in.pdf")
    with pikepdf.open(tmp_path / "in.pdf") as pdf:
        findings, _ = check_integrity(pdf)
    assert any("not an integer" in f.issue for f in findings)
    assert build_tagmap(tmp_path / "in.pdf")["elements"]  # the viewer's map of the original can still be built
