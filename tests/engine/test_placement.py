"""Kept tags: what the engine adds (content the old tags left out, untagged form fields) goes beside the kept tag it
follows on its page, not after everything (found on a fillable form whose page-1 contact details were read after
page 6, with its 119 fields after that)."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.models import PageAnalysis, TextBlock
from app.pipeline.orchestrator import prepare_analysis
from app.pipeline.structure import detect_columns, heuristic_reading_order
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _tagged_form(path):
    """Two pages tagged by their "producer": page 1 has three tagged paragraphs (a label among them), one line the
    tags leave out, a text field under its label and a checkbox left of its label; page 2 has one paragraph."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))

    def line(y, text, mcid=None, x=72):
        show = f"BT /F1 12 Tf {x} {y} Td ({text}) Tj ET".encode()
        return show if mcid is None else f"/P <</MCID {mcid}>> BDC ".encode() + show + b" EMC"

    def page(content):
        return pdf.make_indirect(Dictionary(
            Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
            Contents=pdf.make_stream(b"\n".join(content))))
    p1 = page([line(700, "Instructions for this form", 0), line(660, "Call 555 0100 with questions"),
               line(620, "Name of card holder", 1), line(540, "I agree to the terms", 2, x=100),
               line(480, "Thank you", 3)])
    p2 = page([line(700, "Second page text", 0)])
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([p1, p2]), Count=2))
    p1.Parent = p2.Parent = pdf.Root.Pages

    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    sect = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Sect, P=doc))
    kids1 = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=sect, Pg=p1, K=n)) for n in range(4)]
    sect.K = Array(kids1)
    last = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=p2, K=0))
    doc.K = Array([sect, last])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(kids1), 1, Array([last])])))
    p1.StructParents, p2.StructParents = 0, 1
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)

    def widget(name, rect, ft):
        return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, FT=ft, T=String(name), TU=String(name),
                                            Rect=Array(rect), P=p1, F=4))
    text = widget("Name", [72, 590, 300, 612], Name.Tx)       # just under its label
    box = widget("Agree", [80, 538, 92, 550], Name.Btn)       # left of its label
    p1.Annots = Array([box, text])                             # not in reading order
    pdf.Root.AcroForm = Dictionary(Fields=Array([box, text]))
    pdf.save(path)
    return path


def _outline(path):
    tm = build_tagmap(path)
    out = []

    def walk(i, depth=0):
        e = tm["elements"][i]
        out.append((depth, e["role"], (e.get("text") or "")[:28]))
        for k in e["kids"]:
            walk(k, depth + 1)
    for e in tm["elements"]:
        if e["parent"] is None:
            walk(e["i"])
    return out


def test_added_content_and_fields_go_beside_the_kept_tags_they_follow(tmp_path):
    src = _tagged_form(tmp_path / "form.pdf")
    dst = tmp_path / "out.pdf"
    res = tag_document(src, dst, prepare_analysis(src), title="Form", language="en-US")
    with pikepdf.open(dst) as pdf:
        findings, _ = check_integrity(pdf)
    assert [f for f in findings if f.severity == "error"] == []
    assert res.added_placed == 3
    got = [(role, text) for _, role, text in _outline(dst)]
    assert got == [
        ("Document", "Instructions for this form C"),
        ("Sect", "Instructions for this form C"),
        ("P", "Instructions for this form"),
        ("P", "Call 555 0100 with questions"),   # left out by the old tags: back in its place, not after page 2
        ("P", "Name of card holder"),
        ("Form", ""),                            # the text field, after its label
        ("Form", ""),                            # the checkbox, before the words it's for
        ("P", "I agree to the terms"),
        ("P", "Thank you"),
        ("P", "Second page text"),
    ]


def test_an_added_heading_becomes_a_paragraph_when_the_document_has_its_own_headings(tmp_path):
    """Leftover text in large type isn't promoted over the producer's outline; without one, it's left as found."""
    src = _tagged_form(tmp_path / "form.pdf")
    for with_headings in (True, False):
        path = tmp_path / f"h{int(with_headings)}.pdf"
        with pikepdf.open(src) as pdf:
            if with_headings:
                pdf.Root.StructTreeRoot.K[0].K[0].K[0].S = Name.H1
            pdf.save(path)
        analysis = prepare_analysis(path)
        left_out = next(b for b in analysis.pages[0].blocks if b.text.startswith("Call"))
        left_out.role = "H2"
        res = tag_document(path, tmp_path / "o.pdf", analysis, title="Form", language="en-US")
        role = next(r for _, r, t in _outline(tmp_path / "o.pdf") if t.startswith("Call"))
        assert (role, res.added_headings_demoted) == (("P", 1) if with_headings else (role, 0))
        assert with_headings or role.startswith("H")


def _page(blocks):
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792))
    page.blocks = [TextBlock(id=i, page=0, bbox=bbox, text=text, font_size=11, role="P") for i, bbox, text in blocks]
    return page


def test_columns_can_have_something_across_the_bottom_of_them():
    """Instructions on the left, contact details on the right, then the next section and a barcode across the full
    width underneath: the two columns are still read one after the other."""
    left = [(f"l{n}", (47, 140 + 46 * n, 345, 176 + 46 * n), f"instruction {n}") for n in range(6)]
    right = [(f"r{n}", (390, 164 + 24 * n, 530, 176 + 24 * n), f"contact {n}") for n in range(8)]
    page = _page([("t", (317, 84, 565, 123), "Form title"), *left, *right,
                  ("s", (51, 516, 372, 531), "1. Account owner"), ("a", (72, 555, 122, 564), "Account number"),
                  ("b", (184, 720, 428, 746), "barcode")])
    order = [page.element(e).text for e in heuristic_reading_order(page)]
    assert order == ["Form title", *[f"instruction {n}" for n in range(6)], *[f"contact {n}" for n in range(8)],
                     "1. Account owner", "Account number", "barcode"]
    assert detect_columns(page) == 2


def test_field_tags_have_a_place_and_a_name_in_the_tag_map(tmp_path):
    """A tag that holds only a form field has no content to place it by. It still belongs to a page (the viewer's
    one-page view listed every field on every page), can be shown on it, and is read in order by its name."""
    src = _tagged_form(tmp_path / "form.pdf")
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, prepare_analysis(src), title="Form", language="en-US")
    tm = build_tagmap(dst)
    fields = [e for e in tm["elements"] if e["role"] == "Form"]
    assert [e["name"] for e in fields] == ["Name", "Agree"] and all(e["annot"] for e in fields)
    assert not any(e.get("annot") for e in tm["elements"] if e["role"] != "Form")
    assert all(e["own"] and [b["page"] for b in e["boxes"]] == [1] for e in fields)
    text, box = fields[0]["boxes"][0]["bbox"], fields[1]["boxes"][0]["bbox"]
    assert [round(v) for v in text] == [72, 180, 300, 202] and [round(v) for v in box] == [80, 242, 92, 254]
    # ...and they count in the reading order, between the text around them
    order = [e.get("text") or e.get("name") for e in sorted((e for e in tm["elements"] if e["own"]), key=lambda e: e["order"])]
    assert order == ["Instructions for this form", "Call 555 0100 with questions", "Name of card holder", "Name",
                     "Agree", "I agree to the terms", "Thank you", "Second page text"]
