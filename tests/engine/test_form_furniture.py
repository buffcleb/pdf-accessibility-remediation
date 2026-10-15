"""Forms: page furniture drawn by shared form XObjects, and check marks drawn with a font that isn't embedded
(found on a fillable account form: crop marks and a "DO NOT STAPLE" box on every page, 31 checkboxes)."""
import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.tagging.content_marker import _furniture_extent
from app.tagging.form_marks import redraw_dingbat_marks


def _form(pdf, content: bytes):
    return pdf.make_stream(content, Type=Name.XObject, Subtype=Name.Form, BBox=Array([0, 0, 612, 792]))


def test_a_form_of_lines_and_fills_is_furniture_placed_by_what_it_draws():
    pdf = pikepdf.new()
    marks = _form(pdf, b"q 1 0 0 1 20 20 cm 0 0 m 17 0 l 17 17 l S Q")
    assert sorted(_furniture_extent(marks)) == [(20.0, 20.0), (37.0, 20.0), (37.0, 37.0)]
    # A box with a stamp's text already marked as an artifact is still furniture; a clipping path isn't painted
    stamp = _form(pdf, b"0 0 600 700 re W n 46.8 743 64 18 re f /Artifact <</O /Layout>> BDC BT /F1 8 Tf (DO NOT STAPLE) Tj ET EMC")
    assert sorted(_furniture_extent(stamp)) == [(46.8, 743.0), (46.8, 761.0), (110.8, 743.0), (110.8, 761.0)]
    assert _furniture_extent(_form(pdf, b"")) == []


def test_a_form_with_content_of_its_own_is_not_furniture():
    pdf = pikepdf.new()
    assert _furniture_extent(_form(pdf, b"BT /F1 8 Tf (Read me) Tj ET")) is None          # text
    assert _furniture_extent(_form(pdf, b"q 100 0 0 100 0 0 cm /Im0 Do Q")) is None        # a picture, or another form
    assert _furniture_extent(_form(pdf, b"/P <</MCID 3>> BDC 0 0 10 10 re f EMC")) is None  # tagged content


def _checkbox_pdf(path, showing="/Off", shared=False):
    doc = fitz.open()
    doc.new_page(width=300, height=200)
    doc.save(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        zadb = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.ZapfDingbats))

        def ap(content: bytes):
            return pdf.make_stream(content, Type=Name.XObject, Subtype=Name.Form, BBox=Array([0, 0, 13, 13]),
                                   Resources=Dictionary(Font=Dictionary(ZaDb=zadb)))
        on = b"q\n1 1 11 11 re\nW\nn\nBT\n/ZaDb 11 Tf\n2.03 2.77 Td\n10.6 TL\n0 0 Td\n(4) Tj\nET\nQ\n"
        down = b"q\n0.75 g\n0 0 13 13 re\nf\nQ\nq\nBT\n/ZaDb 11 Tf\n2.03 2.77 Td\n(4) Tj\nET\nQ\n"
        one = ap(on)
        widgets = []
        for n in range(2):
            widgets.append(pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Btn, T=f"box{n}", Rect=Array([40, 150 - 30 * n, 53, 163 - 30 * n]),
                AS=Name(showing if n == 0 else "/Off"), DA="/ZaDb 11 Tf 0 g",
                AP=Dictionary(N=Dictionary(Off=ap(b""), Yes=one if shared else ap(on)),
                              D=Dictionary(Off=ap(b"q 0.75 g 0 0 13 13 re f Q"), Yes=ap(down))))))
        pdf.pages[0].Annots = Array(widgets)
        pdf.Root.AcroForm = Dictionary(Fields=Array(widgets))
        pdf.save(path)
    return path


def _zadb_streams(path) -> int:
    with pikepdf.open(path) as pdf:
        return sum(1 for a in pdf.pages[0].Annots for which in ("/N", "/D") for k in a.AP[which].keys()
                   if b"ZaDb" in a.AP[which][k].read_bytes())


def _pixels(path):
    with fitz.open(path) as doc:
        return doc[0].get_pixmap(dpi=100).samples


def test_check_marks_of_unchecked_boxes_become_shapes_and_the_page_looks_the_same(tmp_path):
    src = _checkbox_pdf(tmp_path / "form.pdf")
    dst = tmp_path / "out.pdf"
    assert _zadb_streams(src) == 4
    assert redraw_dingbat_marks(src, dst) == 2
    assert _zadb_streams(dst) == 0 and _pixels(dst) == _pixels(src)
    with pikepdf.open(dst) as pdf:
        yes = pdf.pages[0].Annots[0].AP.N.Yes
        data = yes.read_bytes()
        assert b" l S" in data and b"Tj" not in data and "/Font" not in yes.Resources
        # ...drawn where the glyph was: inside the box, in the color in force (black)
        assert b"0 G" in data
    # Ticking a box shows a mark in both files
    blank = _pixels(dst)
    for path in (src, dst):
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Annots[0].AS = Name.Yes
            pdf.save(path)
    assert _pixels(src) != blank and _pixels(dst) != blank


def test_a_mark_that_is_showing_is_left_alone(tmp_path):
    """A ticked box keeps its glyph: changing it would change how the page looks. Also when its appearance is
    shared with a box that isn't ticked."""
    src = _checkbox_pdf(tmp_path / "ticked.pdf", showing="/Yes")
    dst = tmp_path / "out.pdf"
    assert redraw_dingbat_marks(src, dst) == 2  # the other box, and this one's pressed appearance
    with pikepdf.open(dst) as pdf:
        assert b"ZaDb" in pdf.pages[0].Annots[0].AP.N.Yes.read_bytes()
        assert b"ZaDb" not in pdf.pages[0].Annots[1].AP.N.Yes.read_bytes()
    assert _pixels(dst) == _pixels(src)
    shared = _checkbox_pdf(tmp_path / "shared.pdf", showing="/Yes", shared=True)
    redraw_dingbat_marks(shared, tmp_path / "out2.pdf")
    with pikepdf.open(tmp_path / "out2.pdf") as pdf:
        assert b"ZaDb" in pdf.pages[0].Annots[1].AP.N.Yes.read_bytes()
    assert _pixels(tmp_path / "out2.pdf") == _pixels(shared)


def test_a_form_without_such_marks_is_not_rewritten(tmp_path):
    doc = fitz.open()
    doc.new_page()
    doc.save(tmp_path / "plain.pdf")
    assert redraw_dingbat_marks(tmp_path / "plain.pdf", tmp_path / "out.pdf") == 0
    assert not (tmp_path / "out.pdf").exists()
