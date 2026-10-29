"""Pages whose print is pictures (an online converter's cover), pictures stacked into one, a page's background picture,
and the headings of a lab manual: a certificate's run-on sentence and one-off title, fractions in larger print."""
import pymupdf as fitz

from app.pipeline.analyzer import analyze_page
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TextBlock
from app.pipeline.orchestrator import _in_picture, _read_pictures_to_background
from app.pipeline.structure.headings import assign_headings


def _picture(page, rect, shade):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), 0)
    pix.set_rect(pix.irect, shade)
    page.insert_image(fitz.Rect(rect), pixmap=pix, keep_proportion=False)


def _cover(path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # A logo built of three layers, then three lines of print placed as pictures, and a photo
    _picture(page, (259, 65, 336, 145), (200, 160, 0))
    _picture(page, (269, 76, 327, 115), (90, 90, 90))
    _picture(page, (251, 133, 276, 147), (230, 200, 0))
    _picture(page, (95, 181, 500, 196), (20, 40, 160))
    _picture(page, (161, 200, 434, 213), (200, 30, 30))
    _picture(page, (115, 217, 480, 227), (10, 10, 10))
    _picture(page, (0, 655, 595, 842), (120, 140, 100))
    doc.save(path)


def test_a_page_of_pictures_of_print_is_read_by_ocr_and_a_layered_logo_is_one_figure(tmp_path):
    _cover(tmp_path / "cover.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "cover.pdf"), 0)
    assert pa.text_pictures and pa.is_scanned
    joined = [i for i in pa.images if i.holds_pieces]
    assert len(joined) == 1
    assert [round(v) for v in joined[0].bbox] == [251, 65, 336, 147]
    assert len(pa.images) == 5  # the logo, three lines, the photo


def test_a_page_with_a_photo_and_no_text_is_not_read(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    _picture(page, (60, 100, 540, 500), (120, 140, 100))
    _picture(page, (60, 520, 540, 545), (10, 10, 10))  # one caption-shaped picture is not a page of print
    doc.save(tmp_path / "photo.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "photo.pdf"), 0)
    assert not pa.text_pictures and not pa.is_scanned


def test_a_picture_behind_the_page_s_text_is_its_background(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    _picture(page, (30, 0, 595, 840), (250, 250, 240))
    for n, line in enumerate(["Certificate", "Certified that this is the record of the work done by",
                              "Name ..............................", "Date: Faculty in charge"]):
        page.insert_text((80, 300 + n * 60), line, fontsize=14)
    doc.save(tmp_path / "certificate.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "certificate.pdf"), 0)
    assert [i.decorative for i in pa.images] == [True]


def test_a_picture_inside_a_drawn_chart_is_part_of_it(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Plenty of body text on the page so that it is not empty.", fontsize=11)
    # Axes and ticks drawn as lines, the curve placed as a picture between them
    page.draw_rect(fitz.Rect(110, 590, 111, 750), color=None, fill=(0, 0, 0))
    page.draw_rect(fitz.Rect(110, 749, 350, 750), color=None, fill=(0, 0, 0))
    for x in range(130, 350, 20):
        page.draw_rect(fitz.Rect(x, 744, x + 1, 750), color=None, fill=(0, 0, 0))
    _picture(page, (142, 596, 244, 718), (30, 30, 30))
    doc.save(tmp_path / "chart.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "chart.pdf"), 0)
    figures = [i for i in pa.images if not i.decorative]
    assert len(figures) == 1 and figures[0].kind == "vector" and figures[0].holds_pieces


def _page(images, blocks=()):
    pa = PageAnalysis(number=0, width=595, height=842, rotation=0, pdf_to_page=(1, 0, 0, 1, 0, 0))
    pa.images = list(images)
    pa.blocks = list(blocks)
    return pa


def test_pictures_ocr_read_become_background_and_what_it_read_in_a_photo_is_left_out():
    line = ImageRegion(id="a", page=0, bbox=(95, 181, 500, 196), xref=5)
    title = ImageRegion(id="b", page=0, bbox=(85, 270, 510, 295), xref=6)  # white on blue: OCR read nothing
    photo = ImageRegion(id="c", page=0, bbox=(0, 655, 595, 842), xref=7)
    pa = _page([line, title, photo])
    assert _in_picture(pa, (250, 700, 330, 710))  # a sign on the building
    assert not _in_picture(pa, (95, 182, 500, 195))
    _read_pictures_to_background(pa, [(95, 182, 500, 195)])
    assert line.decorative and not title.decorative and not photo.decorative


def _block(page, text, size, top, bold=False, fraction=False):
    return TextBlock(id=f"p{page}b{top}", page=page, bbox=(80, top, 500, top + size), text=text, font_size=size,
                     bold=bold, fraction=fraction)


def test_a_certificate_s_title_closes_with_its_page_and_its_run_on_sentence_is_no_heading():
    pages = []
    for n in range(30):
        pa = _page([])
        pa.number = n
        pages.append(pa)
    pages[0].blocks = [_block(0, "Laboratory Manual", 24, 100, bold=True)]
    pages[2].blocks = [_block(2, "Certificate", 46, 300, bold=True),
                       _block(2, "This page records the work carried out in the lab by", 14.5, 400),
                       _block(2, "Mr./Ms ...................... Roll.No ............", 12, 430)]
    pages[6].blocks = [_block(6, "Vision & Mission", 16, 80, bold=True), _block(6, "Vision", 12, 130, bold=True)]
    pages[9].blocks = [_block(9, "M = n/V", 14, 480, fraction=True), _block(9, "Where n = W/M.W", 14, 520, fraction=True)]
    for p in pages:
        p.blocks.append(_block(p.number, "Body text of the page, a sentence long enough to set the body size.", 12, 700))
    doc = DocumentAnalysis(page_count=30, pages=pages, title=None, language=None, has_struct_tree=False,
                           is_marked=False, body_font_size=12)
    assign_headings(doc)
    roles = {b.text: b.role for p in pages for b in p.blocks}
    assert roles["Laboratory Manual"] == "H1" and roles["Certificate"] == "H1"
    assert roles["This page records the work carried out in the lab by"] == "P"
    # (not under the certificate, as they were: H2 and H3)
    assert roles["Vision & Mission"] == "H1" and roles["Vision"] == "H2"
    assert roles["M = n/V"] == "P" and roles["Where n = W/M.W"] == "P"


def test_the_line_under_a_fraction_keeps_its_words(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Plenty of body text on the page so that it is not empty, set in the body size.", fontsize=12)
    # "M = W over M.W", in larger print, with the paragraph's next line set close under it
    page.insert_text((200, 520), "M =", fontsize=14, fontname="tiro")
    page.insert_text((236, 512), "W", fontsize=14, fontname="tiro")
    page.draw_rect(fitz.Rect(230, 516, 262, 516.6), color=None, fill=(0, 0, 0))
    page.insert_text((232, 530), "M.W", fontsize=14, fontname="tiro")
    page.insert_text((88, 541), "Here W stands for the mass in grams and M.W for the mass of one mole.", fontsize=12,
                     fontname="tiro")
    doc.save(tmp_path / "fraction.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "fraction.pdf"), 0)
    texts = [b.text for b in pa.blocks]
    assert any(b.fraction and "gram" not in b.text for b in pa.blocks), texts
    assert any("mass in grams" in t for t in texts), texts


def test_a_scanned_score_with_its_title_typed_on_it_stays_a_figure(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 300), 0)
    pix.set_rect(pix.irect, (255, 255, 255))
    for y in range(20, 300, 12):  # staves all down the page
        pix.set_rect(fitz.IRect(10, y, 190, y + 2), (0, 0, 0))
    page.insert_image(fitz.Rect(20, 12, 575, 830), pixmap=pix, keep_proportion=False)
    for n, line in enumerate(["Piano Sonata No. 14", "Moonlight", "Ludwig van Beethoven"]):
        page.insert_text((200, 40 + n * 16), line, fontsize=12)
    doc.save(tmp_path / "score.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "score.pdf"), 0)
    assert [i.decorative for i in pa.images] == [False]


def test_an_equation_s_pictures_drawn_over_each_other_stay_one_formula(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((113, 120), "The share of particles in each state follows the equation below.", fontsize=12)
    _picture(page, (242, 547, 285, 586), (0, 0, 0))
    _picture(page, (245, 574, 281, 594), (0, 0, 0))
    page.insert_text((450, 572), "Equation 1.1", fontsize=12)
    doc.save(tmp_path / "equation.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "equation.pdf"), 0)
    figures = [i for i in pa.images if not i.decorative]
    assert [(i.kind, i.drawing) for i in figures] == [("formula", "equation")]


def test_a_graph_in_strips_takes_its_sideways_axis_name_and_keeps_its_caption(tmp_path):
    from app.pipeline.orchestrator import prepare_analysis
    from app.pipeline.structure.lists_captions import caption_pairs

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for n in range(12):
        page.insert_text((60, 80 + n * 12), "Body text of the report set in small print, a line of a long paragraph.",
                         fontsize=9)
    # A graph placed as strips that overlap, its axis name set on its side in larger print
    _picture(page, (307, 484, 580, 600), (90, 90, 90))
    _picture(page, (307, 530, 580, 687), (60, 60, 60))
    page.insert_text((325, 640), "Depth (m)", fontsize=11, rotate=90)
    page.insert_text((310, 705), "Figure 12. Mean level of a tracer measured over each two-metre span.", fontsize=9)
    doc.save(tmp_path / "strips.pdf")
    pa = prepare_analysis(tmp_path / "strips.pdf").pages[0]
    graph = next(i for i in pa.images if i.drawing == "layers")
    assert graph.labels == "Depth (m)"
    assert list(caption_pairs(pa).values()) == [graph.id]
