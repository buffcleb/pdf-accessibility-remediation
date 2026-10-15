"""A form XObject that draws itself, or two that draw each other. The content marker tags a form drawn once from
the page from the inside; nothing in a PDF stops such a form from drawing itself, and a file made that way used to
recurse until Python's limit and fail the job."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity


def _pdf_with_forms(path, mutual: bool):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    a = pdf.make_stream(b"", Type=Name.XObject, Subtype=Name.Form, BBox=Array([0, 0, 612, 792]))
    b = pdf.make_stream(b"", Type=Name.XObject, Subtype=Name.Form, BBox=Array([0, 0, 612, 792]))
    if mutual:
        a.write(b"BT /F1 12 Tf 72 700 Td (From form A) Tj ET /Fx2 Do")
        a.Resources = Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fx2=b))
        b.write(b"BT /F1 12 Tf 72 670 Td (From form B) Tj ET /Fx1 Do")
        b.Resources = Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fx1=a))
    else:
        a.write(b"BT /F1 12 Tf 72 700 Td (From form A) Tj ET /Fx1 Do")
        a.Resources = Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fx1=a))
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
        Resources=Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fx1=a)),
        Contents=pdf.make_stream(b"BT /F1 12 Tf 72 740 Td (On the page) Tj ET /Fx1 Do")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)


def _tags(tmp_path, mutual: bool):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _pdf_with_forms(src, mutual)
    tag_document(src, out, prepare_analysis(src), title="Forms", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
    assert [f for f in findings if f.severity == "error"] == []
    return index


def test_a_form_that_draws_itself_is_tagged_once(tmp_path):
    index = _tags(tmp_path, mutual=False)
    assert index.elements  # the job ran to the end, and the page and the form's own text have tags


def test_two_forms_that_draw_each_other_are_tagged_once(tmp_path):
    index = _tags(tmp_path, mutual=True)
    assert index.elements
