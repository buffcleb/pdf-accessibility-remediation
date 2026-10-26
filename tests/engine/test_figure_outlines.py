"""Kept tags: presentation programs tag every text box's border and every arrow-shaped box as a Figure with no
description (about 1,000 of them in a 332-page clinical guideline). A Figure that is only lines around text is
not a picture: the words have their own tags."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.tagmap import build_tagmap
from tests.engine.pdfs import integrity_errors


def _boxes(path):
    """A box drawn around a sentence (Figure, with the sentence's paragraph beside it in the tree), an arrow-shaped
    box whose Figure holds its own paragraph, a small square with nothing in it, and a Figure that draws nothing
    and only wraps a paragraph."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    content = [
        b"/Figure <</MCID 0>> BDC 70 690 300 30 re S EMC",
        b"/P <</MCID 1>> BDC BT /F1 12 Tf 80 700 Td (If present, start treatment) Tj ET EMC",
        b"/Figure <</MCID 2>> BDC 70 590 300 30 re S EMC",
        b"/P <</MCID 3>> BDC BT /F1 12 Tf 80 600 Td (Refer to a specialist) Tj ET EMC",
        b"/Figure <</MCID 4>> BDC 70 500 14 14 re S EMC",
        b"/P <</MCID 5>> BDC BT /F1 12 Tf 100 502 Td (Yes) Tj ET EMC",
    ]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))

    def elem(role, parent, k):
        return pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(role), P=parent, Pg=page, K=k))
    border, sentence = elem("/Figure", doc, 0), elem("/P", doc, 1)
    arrow = elem("/Figure", doc, None)
    inside = elem("/P", arrow, 3)
    arrow.K = Array([2, inside])
    square = elem("/Figure", doc, 4)
    group = elem("/Figure", doc, None)  # draws nothing itself: only wraps the paragraph
    yes = elem("/P", group, 5)
    group.K = Array([yes])
    doc.K = Array([border, sentence, arrow, square, group])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([border, sentence, arrow, inside, square, yes])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)
    return path


def test_lines_around_text_are_not_figures(tmp_path):
    src, out = _boxes(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    result = tag_document(src, out, prepare_analysis(src), title="Boxes", language="en-US", pdfua_part=1)
    assert integrity_errors(out) == []
    elements = build_tagmap(out)["elements"]
    texts = [e["text"] for e in elements if e.get("own") and (e.get("text") or "").strip()]
    assert texts == ["If present, start treatment", "Refer to a specialist", "Yes"]  # every word still read, in order
    figures = [e for e in elements if e["role"] == "Figure"]
    # The two borders are gone; the empty square has nothing to say for itself and still needs a description
    assert len(figures) == 1 and result.figure_outlines_released == 3
    assert len(result.figures_needing_alt) == 1
    # The arrow's Figure and the wrapper each held a paragraph: groupings now, with the paragraph still inside
    assert sum(1 for e in elements if e["role"] == "Div" and e["kids"]) == 2
