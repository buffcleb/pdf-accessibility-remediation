"""Graphs drawn as hundreds of tiny pieces (a curve as a string of dots, or as one path of short segments) are
figures, with their rules and labels, not decoration a screen reader skips. Found on a mathematics-of-music
textbook: 46 of its 531 pages have such graphs, up to 25,000 pieces each."""
import math

import pikepdf
import pymupdf as fitz

from app.pipeline import plots
from app.pipeline.analyzer import analyze_page
from app.pipeline.models import ImageRegion, PageAnalysis, TextBlock
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _speck(n, x, y):
    return ImageRegion(id=f"s{n}", page=0, bbox=(x, y, x + 0.4, y + 0.4), xref=0, decorative=True)


def _sine(x0=150.0, y0=400.0, width=300.0, height=40.0, count=300):
    return [_speck(n, x0 + width * n / count, y0 + height * math.sin(n / 12)) for n in range(count)]


def _block(n, bbox, text):
    return TextBlock(id=f"b{n}", page=0, bbox=bbox, text=text, font_size=9)


def test_a_curve_of_dots_is_one_region_with_its_axis_and_labels():
    axis = ImageRegion(id="axis", page=0, bbox=(150, 400, 450, 400.3), xref=0, decorative=True)
    blocks = [_block(0, (455, 395, 462, 405), "t"), _block(1, (140, 352, 147, 361), "y"),
              _block(2, (150, 470, 450, 482), "Figure 2.1: two tones a few hertz apart, added point by point."),
              _block(3, (100, 300, 500, 340), "A curve like this one can be drawn for any pair of tones.")]
    (box,) = plots.find_dense_regions(_sine() + [axis], blocks, 612, 792)
    assert box[0] <= 141 and box[2] >= 461          # the labels at both ends
    assert 345 < box[1] < 362 and 439 < box[3] < 470  # not the sentence above or the caption below


def test_a_dotted_rule_and_a_few_stray_specks_are_not_a_graph():
    rule = [_speck(n, 100 + n * 2, 300) for n in range(200)]         # dots in a line
    stray = [_speck(n, 100 + (n % 7) * 60, 500 + (n // 7) * 30) for n in range(30)]
    assert plots.find_dense_regions(rule + stray, [], 612, 792) == []


def test_two_graphs_on_a_page_are_two_figures():
    boxes = plots.find_dense_regions(_sine(y0=200) + _sine(y0=600), [], 612, 792)
    assert len(boxes) == 2 and boxes[0][3] < boxes[1][1]


def test_the_figure_takes_the_pieces_and_labels_inside_it():
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792))
    page.images = _sine()
    page.blocks = [_block(0, (455, 395, 462, 405), "t"), _block(1, (100, 300, 500, 340), "A sentence above the graph.")]
    (made,) = plots.add_to_page(page, plots.find_dense_regions(page.images, page.blocks, 612, 792))
    assert page.images == [made] and made.holds_pieces and not made.decorative and made.labels == "t"
    assert [b.text for b in page.blocks] == ["A sentence above the graph."]


def _dotted_graph(path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 200), "When two tones are added the result looks like this.", fontsize=11, fontname="helv")
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1, 1), False)
    pix.clear_with(0)
    for n in range(240):
        x, y = 150 + n * 1.25, 400 + 40 * math.sin(n / 12)
        page.insert_image(fitz.Rect(x, y, x + 0.5, y + 0.5), pixmap=pix)
    page.insert_text((455, 404), "t", fontsize=9, fontname="helv")
    page.insert_text((150, 480), "Figure 2.1: two tones a few hertz apart, added point by point.", fontsize=9, fontname="helv")
    doc.save(path)
    return path


def test_a_dotted_graph_is_tagged_as_one_figure(tmp_path):
    src = _dotted_graph(tmp_path / "g.pdf")
    analysis = prepare_analysis(src)
    (figure,) = [i for i in analysis.pages[0].images if not i.decorative]
    assert figure.holds_pieces and figure.labels == "t" and len(analysis.pages[0].images) == 1
    figure.alt = "A wave whose height rises and falls."
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, analysis, title="Graph", language="en-US")
    with pikepdf.open(dst) as pdf:
        findings, _ = check_integrity(pdf)
    assert [f for f in findings if f.severity == "error"] == []
    tm = build_tagmap(dst)
    (tagged,) = [e for e in tm["elements"] if e["role"] == "Figure"]
    inside = [p for p in tm["pieces"] if p["el"] == tagged["i"]]
    assert {p["kind"] for p in inside} == {"image", "text"}       # the dots and the label "t"
    assert not [a for a in tm["artifacts"] if "image" in a["kinds"]]  # no dot left as decoration
    assert len([e for e in tm["elements"] if e["role"] in ("P", "Caption")]) == 2  # the sentence and the caption
    with fitz.open(src) as a, fitz.open(dst) as b:
        assert a[0].get_pixmap(dpi=72).samples == b[0].get_pixmap(dpi=72).samples


def test_a_curve_drawn_as_one_long_path_is_a_figure(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 200), "A graph follows, drawn as a single path.", fontsize=11, fontname="helv")
    shape = page.new_shape()
    shape.draw_polyline([fitz.Point(200 + n * 1.5, 450 + 40 * math.sin(n / 10)) for n in range(140)])
    shape.finish(width=0.5)
    shape.draw_line((200, 450), (410, 450))
    shape.finish(width=0.4)
    shape.commit()
    doc.save(tmp_path / "c.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "c.pdf"), 0)
    (figure,) = [i for i in pa.images if not i.decorative]
    assert figure.kind == "vector" and 195 < figure.bbox[0] < 205 and figure.bbox[2] > 405
