"""#435: a whole page of text placed as one Form XObject and tagged as one Figure (as some files assemble pages) is
read as a picture: its text is lost to a screen reader. With the document's own tags kept, that text is tagged afresh
from the layout, inside the form; a form that's a picture with a few labels keeps its figure."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.analyzer import analyze_document
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap

LINES = ["Education for patients"] + [f"Item {n}: the clinic explains the plan and its next steps." for n in range(24)]


def _page(tmp_path, lines):
    """A page whose body is one form of text, marked as one MCID on the page and tagged as one Figure."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    fonts = Dictionary(F1=font)
    body = "".join(f"BT /F1 {16 if n == 0 else 10} Tf 72 {740 - 22 * n} Td ({t}) Tj ET\n"
                   for n, t in enumerate(lines))
    form = pdf.make_stream(body.encode(), Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 612, 792],
                           Resources=Dictionary(Font=fonts))
    page = pikepdf.Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792], StructParents=0,
                              Resources=Dictionary(Font=fonts, XObject=Dictionary(Fm0=form)),
                              Contents=pdf.make_stream(b"/P <</MCID 0>> BDC /Fm0 Do EMC\n"))
    pdf.pages.append(pikepdf.Page(page))
    pg = pdf.pages[0].obj
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    fig = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=doc, Pg=pg, K=0))
    doc.K = Array([fig])
    root.K = doc
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([fig])])))
    root.ParentTreeNextKey = 1
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "src.pdf"
    pdf.save(src)
    return src


def _roles(path):
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        return [e.role for e in index.elements]


def test_a_page_of_text_tagged_as_one_figure_is_tagged_from_the_layout(tmp_path):
    src = _page(tmp_path, LINES)
    out = tmp_path / "out.pdf"
    result = tag_document(src, out, analyze_document(src), title="Education", language="en-US")
    assert result.existing_tags["usable"]  # the document's own tags were kept
    assert result.form_pages_retagged == 1
    roles = _roles(out)
    assert "Figure" not in roles and roles.count("P") + sum(r.startswith("H") for r in roles) >= 10
    with pikepdf.open(out) as pdf:
        _, index = check_integrity(pdf)
        assert b"/MCID" in pdf.pages[0].Resources.XObject.Fm0.read_bytes()  # tagged inside the form
    tm = build_tagmap(out)
    texts = " ".join(e.get("text") or "" for e in tm["elements"] if e["role"] in ("P", "H1", "H2"))
    assert "Item 23" in texts and all(e.get("boxes") for e in tm["elements"] if e["role"] == "P")


def test_a_form_that_is_a_picture_with_a_few_labels_keeps_its_figure(tmp_path):
    src = _page(tmp_path, ["Step 1", "Step 2", "Step 3"])
    out = tmp_path / "out.pdf"
    result = tag_document(src, out, analyze_document(src), title="Steps", language="en-US")
    assert result.form_pages_retagged == 0 and "Figure" in _roles(out)


def test_a_kept_grouping_tag_holding_only_a_shape_is_decoration(tmp_path):
    """#438: an Art (or Sect, Div, Part) whose only content is a bar drawn down the page is decoration."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    page = pikepdf.Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792], StructParents=0,
                              Resources=Dictionary(Font=Dictionary(F1=font)),
                              Contents=pdf.make_stream(
                                  b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (A paragraph of the page.) Tj ET EMC\n"
                                  b"/Art <</MCID 1>> BDC 0 0 0 rg 580 0 20 792 re f EMC\n"))
    pdf.pages.append(pikepdf.Page(page))
    pg = pdf.pages[0].obj
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=pg, K=0))
    art = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Art, P=doc, Pg=pg, K=1))
    doc.K = Array([para, art])
    root.K = doc
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para, art])])))
    root.ParentTreeNextKey = 1
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "bar.pdf"
    pdf.save(src)
    out = tmp_path / "out.pdf"
    result = tag_document(src, out, analyze_document(src), title="Bar", language="en-US")
    assert result.existing_tags["usable"] and result.groupings_artifacted == 1
    roles = _roles(out)
    assert "Art" not in roles and "P" in roles
    with pikepdf.open(out) as done:
        assert b"/Artifact" in done.pages[0].Contents.read_bytes()
