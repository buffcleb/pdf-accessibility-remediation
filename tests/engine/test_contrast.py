"""#367: text below WCAG 1.4.3's contrast minimum against what it's drawn on, listed per page for review."""
from __future__ import annotations

import pymupdf as fitz
import pytest

from app.verification import contrast


def test_the_wcag_ratio():
    assert contrast.ratio((0, 0, 0), (1, 1, 1)) == pytest.approx(21.0)
    assert contrast.ratio((1, 1, 1), (1, 1, 1)) == pytest.approx(1.0)
    assert contrast.ratio((0.4627, 0.4627, 0.4627), (1, 1, 1)) == pytest.approx(4.54, abs=0.02)  # #767676, the 4.5 line


@pytest.fixture
def page_of_colors(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Black on white is fine", fontsize=12)
    p.insert_text((72, 130), "Mid gray at 4.5 is fine", fontsize=12, color=(0.4627, 0.4627, 0.4627))
    p.insert_text((72, 160), "Light gray on white is faint", fontsize=12, color=(0.75, 0.75, 0.75))
    p.draw_rect(fitz.Rect(60, 180, 420, 240), color=None, fill=(0.1, 0.2, 0.5))
    p.insert_text((72, 210), "White on dark blue is fine", fontsize=12, color=(1, 1, 1))
    p.insert_text((72, 232), "Gray on dark blue large", fontsize=20, color=(0.35, 0.35, 0.45))
    p.insert_text((72, 300), "Invisible OCR text", fontsize=12, render_mode=3)
    p.insert_text((72, 330), "Transparent hidden text", fontsize=12, fill_opacity=0)
    p.insert_text((72, 360), "Covered by a box", fontsize=12)
    p.draw_rect(fitz.Rect(60, 345, 300, 370), color=None, fill=(0.2, 0.0, 0.45))  # drawn after: hides it
    doc.new_page().insert_text((72, 100), "A page with nothing wrong", fontsize=12)
    path = tmp_path / "colors.pdf"
    doc.save(path)
    return path


def test_faint_text_is_listed_once_per_page_with_colors_and_ratio(page_of_colors):
    (f,) = contrast.check(page_of_colors, 10)
    assert (f.page, f.severity, f.layer, f.issue) == (1, "review", "visual", "text with low contrast")
    assert f.extra["count"] == 2  # the light gray, and the large gray on blue (under 3:1); nothing else
    assert "“Light gray on white is faint” #BFBFBF on #FFFFFF, 1.8:1 (needs 4.5:1)" in f.detail
    assert "Gray on dark blue large" in f.detail and "needs 3:1" in f.detail
    assert "fine" not in f.detail and "Invisible" not in f.detail and "Transparent" not in f.detail
    assert "Covered" not in f.detail
    # Where the two faint runs are, for Show (#426): the light gray line, and the large gray one on the blue
    assert len(f.boxes) == 2 and 140 < f.boxes[0][1] < 165 and 210 < f.boxes[1][1] < 240


def test_text_over_a_photograph_isnt_guessed_at(tmp_path):
    doc = fitz.open()
    p = doc.new_page()
    noise = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 40), False)
    import random
    random.seed(1)
    noise.set_rect(noise.irect, (128, 128, 128))
    for x in range(200):
        for y in range(40):
            noise.set_pixel(x, y, tuple(random.randrange(256) for _ in range(3)))
    p.insert_image(fitz.Rect(60, 80, 360, 140), pixmap=noise)
    p.insert_text((72, 115), "Caption over a busy photo", fontsize=12, color=(0.6, 0.6, 0.6))
    path = tmp_path / "photo.pdf"
    doc.save(path)
    assert contrast.check(path, 10) == []


def test_only_the_first_pages_are_read(page_of_colors):
    assert contrast.check(page_of_colors, 0) == []
