"""Pages turned with /Rotate, and a web page captured as screenshots (a support article): each page one picture inside
white margins, turned a quarter, with boxes drawn round lines as markup. The first page wasn't read at all (its picture
covers 78% of the page, under the bar for a scan), the second's lines were each taken for turned text and never joined,
the tag manager's boxes were half a turn off, and both pages were said to be cut off."""
import pikepdf
import pymupdf as fitz

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.semantic import check_semantics
from app.verification.tagmap import build_tagmap

LINES = ["The first line of a paragraph about the weather, set across",
         "the page, and the second line of the same paragraph, which",
         "ends here."]


def _picture(width=300, height=200):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 220, 240))
    return pix


def _turned(path, *, text=True, markup=False):
    """A /Rotate 90 page whose text reads across the screen: written up the unrotated page, a line further down the
    screen further right on it. Behind it a picture filling the page."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, pixmap=_picture(), keep_proportion=False)
    if text:
        for n, line in enumerate(LINES):
            page.insert_text((100 + 14 * n, 700), line, fontsize=11, fontname="helv", rotate=90)
    page.set_rotation(90)
    if markup:
        # A box drawn round the first two lines on screen (in unrotated space: across x, up y)
        annot = page.add_rect_annot(fitz.Rect(88, 300, 125, 705) * page.derotation_matrix * page.rotation_matrix)
        annot.set_colors(stroke=(1, 1, 0))
        annot.update()
    doc.save(path)
    return path


def test_lines_reading_across_a_turned_page_join_into_a_paragraph(tmp_path):
    page = prepare_analysis(_turned(tmp_path / "turned.pdf")).pages[0]
    assert (page.width, page.height) == (792, 612)
    body = [b for b in page.blocks if b.text.strip()]
    assert len(body) == 1 and body[0].text.startswith("The first line") and body[0].text.endswith("ends here.")
    x0, y0, x1, y1 = body[0].bbox  # in visual space: across the top left of the screen
    assert 85 < x0 < 100 and 85 < y0 < 100 and x1 > 300 and y1 < 140


def test_tag_map_boxes_on_a_turned_page_are_where_the_text_is_seen(tmp_path):
    src = _turned(tmp_path / "turned.pdf")
    dst = tmp_path / "out.pdf"
    analysis = prepare_analysis(src)
    tag_document(src, dst, analysis, title="Turned", language="en-US")
    seen = next(b for b in analysis.pages[0].blocks if b.text.strip()).bbox
    p = next(e for e in build_tagmap(dst)["elements"] if e["role"] == "P" and e.get("own"))
    box = p["boxes"][0]["bbox"]
    assert all(abs(a - b) < 4 for a, b in zip(box, seen)), (box, seen)  # glyph heights differ a little


def test_a_turned_page_filled_by_its_picture_is_not_cut_off(tmp_path):
    src = _turned(tmp_path / "turned.pdf")
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, prepare_analysis(src), title="Turned", language="en-US")
    with pikepdf.open(dst) as pdf:
        _, index = check_integrity(pdf)
        findings, _ = check_semantics(dst, pdf, index)
    assert "part of the page is cut off" not in {f.issue for f in findings}


def test_a_picture_inside_margins_on_a_page_without_text_is_read(tmp_path):
    def page(name, rect):
        doc = fitz.open()
        p = doc.new_page(width=612, height=792)
        p.insert_image(rect, pixmap=_picture(), keep_proportion=False)
        doc.save(tmp_path / name)
        return tmp_path / name

    # A screenshot placed with wide margins: 78% of the page
    assert prepare_analysis(page("screenshot.pdf", fitz.Rect(66, 0, 546, 792))).pages[0].is_scanned
    # A picture on a third of an otherwise empty page is a picture
    assert not prepare_analysis(page("small.pdf", fitz.Rect(72, 72, 400, 400))).pages[0].is_scanned


def test_a_box_drawn_round_text_says_what_it_marks(tmp_path):
    src = _turned(tmp_path / "marked.pdf", markup=True)
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, prepare_analysis(src), title="Marked", language="en-US")
    with pikepdf.open(dst) as pdf:
        (annot,) = pdf.pages[0].Annots
        assert str(annot.Contents).startswith("Box drawn around: The first line")
    # Tagged again (the engine's own stand-in from an earlier run): described from its text too
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Annots[0].Contents = pikepdf.String("Square annotation")
        pdf.save(src)
    tag_document(src, dst, prepare_analysis(src), title="Marked", language="en-US")
    with pikepdf.open(dst) as pdf:
        assert str(pdf.pages[0].Annots[0].Contents).startswith("Box drawn around: ")


def test_a_box_round_nothing_keeps_its_plain_name(tmp_path):
    src = _turned(tmp_path / "empty.pdf", text=False, markup=True)
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, prepare_analysis(src), title="Box", language="en-US")
    with pikepdf.open(dst) as pdf:
        assert str(pdf.pages[0].Annots[0].Contents) == "Square annotation"
