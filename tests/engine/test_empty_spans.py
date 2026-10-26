"""Kept tags: a Span around nothing a reader would notice (the line break after a form label, found 39 times on a
fillable form made by a layout program) is folded into the tag it's in."""
import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.tagmap import build_tagmap
from tests.engine.pdfs import integrity_errors


def _labels(path):
    """One page as a layout program tags it: two labels, each a paragraph whose text is followed by a Span around a
    single space (its ActualText a line break), and a third paragraph with Spans that do say something."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))

    def show(tag, mcid, x, y, text):
        return f"/{tag} <</MCID {mcid}>> BDC BT /F1 12 Tf {x} {y} Td ({text}) Tj ET EMC".encode()
    content = [show("P", 0, 72, 700, "Account number"), show("Span", 1, 160, 700, " "),
               show("P", 2, 72, 660, "Telephone number"), show("Span", 3, 172, 660, " "),
               show("P", 4, 72, 620, "Read the"), show("Span", 5, 124, 620, "terms"),
               show("Span", 6, 160, 620, " "), show("Span", 7, 166, 620, " "),
               b"/Span <</MCID 8>> BDC 72 580 40 2 re f EMC"]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages

    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    owners = {}

    def elem(role, parent, **more):
        return pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(role), P=parent, Pg=page, **more))

    def para(mcid, *spans):
        p = elem("/P", doc)
        kids = [mcid]
        owners[mcid] = p
        for n, more in spans:
            s = elem("/Span", p, K=n, **more)
            owners[n] = s
            kids.append(s)
        p.K = Array(kids)
        return p
    first = para(0, (1, dict(ActualText=String("\n"))))
    second = para(2, (3, {}))
    third = para(4, (5, {}),                          # visible text
                 (6, dict(Alt=String("see page 2"))),  # says something of its own
                 (7, dict(ID=String("note-1"))),       # something may point at it
                 (8, {}))                              # a drawn line, not text
    doc.K = Array([first, second, third])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([owners[n] for n in range(9)])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)
    return path


def _paragraphs(path):
    tm = build_tagmap(path)
    by_i = {e["i"]: e for e in tm["elements"]}
    return [(e["role"], [by_i[k]["role"] for k in e["kids"]],
             sorted(p["mcid"] for p in tm["pieces"] if p["el"] == e["i"]))
            for e in tm["elements"] if e["role"] == "P"]


def test_spans_around_nothing_visible_are_folded_into_their_tag(tmp_path):
    src = _labels(tmp_path / "labels.pdf")
    dst = tmp_path / "out.pdf"
    res = tag_document(src, dst, prepare_analysis(src), title="Labels", language="en-US")
    assert res.empty_spans_removed == 2
    assert integrity_errors(dst) == []
    first, second, third = _paragraphs(dst)
    # The space is still tagged, as part of the label it follows
    assert first == ("P", [], [0, 1]) and second == ("P", [], [2, 3])
    # Spans with visible text, an Alt, an ID or a drawing are the producer's to keep
    assert third[1] == ["Span"] * 4 and third[2] == [4]


def test_the_pages_look_the_same(tmp_path):
    src = _labels(tmp_path / "labels.pdf")
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, prepare_analysis(src), title="Labels", language="en-US")
    with fitz.open(src) as a, fitz.open(dst) as b:
        assert a[0].get_pixmap(dpi=72).samples == b[0].get_pixmap(dpi=72).samples
        # The words are the same (the line break one Span stood for is a space again)
        assert a[0].get_text().split() == b[0].get_text().split()
