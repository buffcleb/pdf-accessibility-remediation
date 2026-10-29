"""Figures inside scanned pages: found from ink that isn't text, drawn again on their own (clipped) so the page
looks exactly the same, and placed where they show for tagging."""
from __future__ import annotations

import fitz
import pikepdf

from app.pipeline import scan_figures as sf
from app.pipeline.models import PageAnalysis, TextBlock

W, H = 595, 842
TEXT = [(72, 100 + 18 * i, 520, 111 + 18 * i) for i in range(6)]  # what OCR would have read as lines
CHART = (100, 300, 400, 480)
CAPTION = (100, 488, 330, 498)


def _scan(tmp_path, chart=True, extras=None):
    """A scanned page: a picture of text lines, a bar chart and its caption, as one full-page image."""
    src = fitz.open()
    p = src.new_page(width=W, height=H)
    for i in range(6):
        p.insert_text((72, 110 + 18 * i), "Accessible documents let people read content in order " * 1,
                      fontsize=11, fontname="helv")
    if chart:
        p.draw_rect(fitz.Rect(*CHART), color=None, fill=(0.85, 0.85, 0.85))
        for k, hgt in enumerate((40, 80, 120, 160)):
            x = CHART[0] + 20 + 70 * k
            p.draw_rect(fitz.Rect(x, CHART[3] - hgt, x + 40, CHART[3]), color=None, fill=(0.2, 0.35, 0.6))
        p.insert_text((CAPTION[0], CAPTION[3] - 2), "Figure 1: Remediation progress by quarter", fontsize=9,
                      fontname="helv")
    for f in extras or []:
        f(p)
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(page.rect, stream=src[0].get_pixmap(dpi=150).tobytes("png"))
    path = tmp_path / "scan.pdf"
    doc.save(path)
    return path


def _text_boxes(chart=True):
    return TEXT + ([CAPTION] if chart else [])


def test_the_chart_is_found_and_the_caption_left_out(tmp_path):
    page = fitz.open(_scan(tmp_path))[0]
    regions = sf.find_regions(page, _text_boxes())
    assert len(regions) == 1
    x0, y0, x1, y1 = regions[0]
    assert abs(x0 - CHART[0]) < 6 and abs(y0 - CHART[1]) < 6 and abs(x1 - CHART[2]) < 6 and abs(y1 - CHART[3]) < 6
    assert y1 < CAPTION[1]  # the caption stays text


def test_text_rules_and_specks_are_not_figures(tmp_path):
    def rule(p):
        p.draw_line((72, 250), (520, 250), color=(0, 0, 0), width=1)

    def specks(p):
        for x in range(80, 500, 37):
            p.draw_circle((x, 600 + (x % 50)), 0.8, color=None, fill=(0, 0, 0))

    page = fitz.open(_scan(tmp_path, chart=False, extras=[rule, specks]))[0]
    assert sf.find_regions(page, _text_boxes(chart=False)) == []


def test_a_misread_picture_joins_its_region(tmp_path):
    """OCR's misread "word" over the bars (dropped as a picture) helps join the chart into one region, without
    stretching it over the caption."""
    page = fitz.open(_scan(tmp_path))[0]
    hint = (CHART[0] + 5, CHART[1] + 30, CHART[2] - 5, CAPTION[3])
    regions = sf.find_regions(page, _text_boxes(), [hint])
    assert len(regions) == 1 and regions[0][3] < CAPTION[1]


def test_the_redraw_looks_exactly_the_same(tmp_path):
    src = _scan(tmp_path)
    regions = {0: sf.find_regions(fitz.open(src)[0], _text_boxes())}
    out = tmp_path / "out.pdf"
    assert sf.add_redraws(str(src), str(out), regions) == regions
    a, b = fitz.open(src), fitz.open(out)
    for dpi in (50, 72, 150):
        assert a[0].get_pixmap(dpi=dpi, alpha=False).samples == b[0].get_pixmap(dpi=dpi, alpha=False).samples
    # The scan is drawn a second time, clipped to the figure
    with pikepdf.open(out) as pdf:
        ops = [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0])]
    assert ops.count("Do") == 2 and "W" in ops


def test_the_clipped_redraw_is_placed_where_it_shows(tmp_path):
    from app.tagging.content_marker import ContentMarker
    src = _scan(tmp_path)
    region = sf.find_regions(fitz.open(src)[0], _text_boxes())[0]
    out = tmp_path / "out.pdf"
    sf.add_redraws(str(src), str(out), {0: [region]})
    seen = []

    def resolver(kind, bbox, xref):
        if kind == "image":
            seen.append(bbox)
        return ("artifact:Layout", "Artifact")

    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        to_page = (1, 0, 0, -1, 0, float(page.mediabox[3]))
        ContentMarker(pdf, page, to_page, resolver).run()
    full, clipped = seen
    assert full[2] - full[0] > 590 and full[3] - full[1] > 830  # the whole scan
    assert all(abs(a - b) < 1 for a, b in zip(clipped, region))  # the figure


def test_regions_become_figures_and_their_labels_go_with_them():
    page = PageAnalysis(number=0, width=W, height=H, rotation=0, pdf_to_page=(1, 0, 0, 1, 0, 0))
    label = TextBlock(id="b1", page=0, bbox=(120, 460, 150, 470), text="Q1", font_size=7)
    caption = TextBlock(id="b2", page=0, bbox=CAPTION, text="Figure 1: Remediation", font_size=9, role="Caption")
    body = TextBlock(id="b3", page=0, bbox=TEXT[0], text="Accessible documents", font_size=11)
    page.blocks = [label, caption, body]
    assert sf.add_to_page(page, [CHART, (100, 600, 300, 700)]) == 2
    assert [i.id for i in page.images] == ["p0s0", "p0s1"] and all(i.xref == 0 for i in page.images)
    assert [b.id for b in page.blocks] == ["b2", "b3"]


def test_the_tag_viewer_outlines_only_the_figure(tmp_path):
    """The verifier's boxes (and so the tag manager's outlines) follow the clip: the Figure is the chart, not the
    whole scan it's drawn from."""
    from app.verification.tagmap import build_tagmap

    src = _scan(tmp_path)
    doc = fitz.open(src)
    name = doc[0].get_images(full=True)[0][7]
    x0, y0, x1, y1 = CHART
    content = (f"/Artifact BMC q {W} 0 0 {H} 0 0 cm /{name} Do Q EMC\n"
               f"/Figure <</MCID 0>> BDC q {x0} {H - y1} {x1 - x0} {y1 - y0} re W n {W} 0 0 {H} 0 0 cm /{name} Do Q EMC\n")
    xref = doc[0].get_contents()[0]
    doc.update_stream(xref, content.encode())
    doc.save(tmp_path / "tagged.pdf")
    with pikepdf.open(tmp_path / "tagged.pdf", allow_overwriting_input=True) as pdf:
        page = pdf.pages[0].obj
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        doc_el = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document, P=root))
        fig = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Figure, P=doc_el,
                                                   Pg=page, K=0, Alt="Bar chart"))
        doc_el.K = pikepdf.Array([fig])
        root.K = doc_el
        root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([fig])])))
        page.StructParents = 0
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.save(tmp_path / "tagged.pdf")
    tm = build_tagmap(tmp_path / "tagged.pdf")
    box = next(e for e in tm["elements"] if e["role"] == "Figure")["boxes"][0]["bbox"]
    assert all(abs(a - b) < 1 for a, b in zip(box, CHART)), box


def _poster(tmp_path):
    """A page printed as one picture, an assembly poster: two solid drawings (a motor, a board) a dashed wire runs
    between, and a schematic drawn in thin lines below them."""
    src = fitz.open()
    p = src.new_page(width=W, height=H)
    p.draw_rect(fitz.Rect(60, 120, 200, 300), color=None, fill=(0.3, 0.3, 0.3))            # the motor
    p.draw_rect(fitz.Rect(300, 100, 540, 420), color=None, fill=(0.15, 0.35, 0.7))         # the board
    p.draw_line((200, 210), (300, 210), color=(0.4, 0.4, 0.4), width=1, dashes="[3 3] 0")   # the wire
    p.draw_line((540, 300), (560, 700), color=(0.4, 0.4, 0.4), width=1, dashes="[3 3] 0")   # another, down the page
    for k in range(4):                                                                      # a switch schematic
        x = 80 + 60 * k
        p.draw_line((x, 520), (x + 40, 500), color=(0, 0, 0), width=1)
        p.draw_line((x - 10, 520), (x + 50, 520), color=(0, 0, 0), width=1)
        p.draw_rect(fitz.Rect(x + 12, 560, x + 22, 570), color=None, fill=(0, 0, 0))
        p.draw_line((x + 17, 520), (x + 17, 560), color=(0, 0, 0), width=1)
    p.draw_line((60, 600), (320, 600), color=(0, 0, 0), width=1)
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(page.rect, stream=src[0].get_pixmap(dpi=150).tobytes("png"))
    path = tmp_path / "poster.pdf"
    doc.save(path)
    return path


def test_drawings_a_thin_line_joins_are_separate_figures(tmp_path):
    """Production (the Assembly Poster): dashed wiring joined every drawing into one region over the whole page, one
    figure. A region over most of the page is split: its solid drawings with thin lines taken out, and what's left
    in clusters (a schematic in thin lines). A region that size that doesn't come apart is kept whole."""
    page = fitz.open(_poster(tmp_path))[0]
    regions = sf.find_regions(page, [])
    near = lambda r, box: all(abs(a - b) < 10 for a, b in zip(r, box))  # noqa: E731
    assert any(near(r, (60, 120, 200, 300)) for r in regions), regions
    assert any(near(r, (300, 100, 540, 420)) for r in regions), regions
    assert any(r[1] > 450 and r[3] < 640 and r[0] < 100 for r in regions), regions  # the schematic
    assert not any(sf.bbox_area(r) > 0.4 * W * H for r in regions)
    # One picture over most of the page, nothing joined by lines: one region still
    def photo(p):
        p.draw_rect(fitz.Rect(60, 300, 535, 760), color=None, fill=(0.4, 0.5, 0.3))  # 44% of the page: over the share split
    whole = fitz.open(_scan(tmp_path, chart=False, extras=[photo]))[0]
    assert [r for r in sf.find_regions(whole, TEXT) if r[1] > 250] == [(57.0, 297.0, 537.0, 762.0)]
