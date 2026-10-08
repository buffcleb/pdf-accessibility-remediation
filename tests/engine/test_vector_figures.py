"""Vector drawings: page furniture must not swallow figures, and small many-shape drawings count."""
import pymupdf as fitz

from app.pipeline.analyzer import analyze_page


def _page_with_frame_board_and_text(path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Page frame: two long vertical rules drawn as one stroke-only path (as in the ScientificNotation file)
    shape = page.new_shape()
    shape.draw_line((85, 54), (85, 799))
    shape.draw_line((510, 54), (510, 799))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    # A small 8x8 chess board beside a wrapped paragraph
    for r in range(8):
        for c in range(8):
            if (r + c) % 2:
                page.draw_rect(fitz.Rect(140 + c * 7, 440 + r * 7, 147 + c * 7, 447 + r * 7), color=None, fill=(0.6, 0.6, 0.6))
    page.draw_rect(fitz.Rect(136, 436, 200, 500), color=(0, 0, 0), width=0.5)
    page.insert_text((99, 450), "14.", fontsize=11)
    for n, line in enumerate(["Centuries ago, a man promised to give his wife", "some grains of rice on a chess board.",
                              "He placed one grain on the first square."]):
        page.insert_text((210, 450 + n * 14), line, fontsize=11)
    page.insert_text((99, 300), "Plenty of body text on the page so that it is not empty.", fontsize=11)
    doc.save(path)


def test_page_frame_does_not_swallow_small_figure(tmp_path):
    path = tmp_path / "frame.pdf"
    _page_with_frame_board_and_text(path)
    pa, _ = analyze_page(fitz.open(path), 0)
    vectors = [i.bbox for i in pa.images if i.kind == "vector"]
    assert len(vectors) == 1, vectors
    x0, y0, x1, y1 = vectors[0]
    assert 130 <= x0 and x1 <= 205 and 430 <= y0 and y1 <= 505, "figure box should hug the chess board"


def test_shaded_box_with_text_is_not_a_figure(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(108, 600, 506, 640), color=None, fill=(0.85, 0.85, 0.85))
    page.draw_rect(fitz.Rect(108, 600, 235, 640), color=None, fill=(0.9, 0.9, 0.9))
    page.draw_rect(fitz.Rect(235, 600, 506, 640), color=None, fill=(0.9, 0.9, 0.9))
    page.insert_text((112, 618), "quadratic formula", fontsize=10)
    page.insert_text((270, 625), "x = (-b +/- sqrt(b^2 - 4ac)) / 2a", fontsize=14)
    doc.save(tmp_path / "shaded.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "shaded.pdf"), 0)
    assert not [i for i in pa.images if i.kind == "vector"]
