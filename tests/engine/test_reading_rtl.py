"""#368: a page mostly in Arabic or Hebrew is read from the right: its columns right to left, and items side by side
in a row from the right. Left-to-right pages are unchanged, and the review asks a person to check such pages."""
from pathlib import Path

import pymupdf as fitz

from app.pipeline.models import PageAnalysis, TextBlock
from app.pipeline.structure import heuristic_reading_order, right_to_left
from app.pipeline.structure.reading_order import _by_rows
from app.verification.verify import _rtl_findings

HEBREW = "שלום עולם זהו טקסט בעברית לבדיקה של כיוון הקריאה בעמוד"
ARABIC = "مرحبا بالعالم هذا نص باللغة العربية لاختبار اتجاه القراءة في الصفحة"
ENGLISH = "This paragraph is written in English and is read from the left as usual"


def _page(text: str, blocks):
    return PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792),
                        blocks=[TextBlock(id=i, page=0, bbox=bbox, text=f"{text} {i}", font_size=11, role="P")
                                for i, bbox in blocks])


# A title across the top, then two columns: two paragraphs and a short line at the foot of each
LAYOUT = [("title", (72, 60, 540, 90)),
          ("left1", (72, 120, 290, 300)), ("left2", (72, 310, 290, 500)),
          ("right1", (320, 120, 540, 300)), ("right2", (320, 310, 540, 500)),
          ("foot_left", (72, 700, 200, 715)), ("foot_right", (412, 700, 540, 715))]


def test_which_pages_are_right_to_left():
    assert right_to_left(HEBREW) and right_to_left(ARABIC)
    assert not right_to_left(ENGLISH)
    assert not right_to_left("שלום")                        # too little to tell
    assert not right_to_left(ENGLISH + " " + ENGLISH + " שלום עולם")  # a phrase in English text
    assert right_to_left(ARABIC + " PDF 2026 ISO")          # Arabic text with a few Latin words and numbers


def test_columns_and_rows_are_read_from_the_right():
    assert heuristic_reading_order(_page(ARABIC, LAYOUT)) == [
        "title", "right1", "right2", "foot_right", "left1", "left2", "foot_left"]
    assert heuristic_reading_order(_page(HEBREW, LAYOUT))[1] == "right1"
    # The same layout in English: left to right, as before
    assert heuristic_reading_order(_page(ENGLISH, LAYOUT)) == [
        "title", "left1", "left2", "foot_left", "right1", "right2", "foot_right"]
    # Items sharing a row (a label beside its value): from the right
    row = [("a", (72, 100, 150, 112)), ("b", (300, 101, 400, 113)), ("c", (72, 130, 150, 142))]
    assert [i for i, _ in _by_rows(row, rtl=True)] == ["b", "a", "c"]
    assert [i for i, _ in _by_rows(row)] == ["a", "b", "c"]


def test_the_review_names_the_right_to_left_pages(tmp_path, monkeypatch):
    # PyMuPDF's built-in fonts can't draw Hebrew, so pages say RTL instead, and the detection (tested above) is told so
    import app.pipeline.structure as structure

    monkeypatch.setattr(structure, "right_to_left", lambda text: "RTL" in text)
    doc = fitz.open()
    for text in ("LTR page", "RTL page", "RTL page"):
        doc.new_page().insert_text((72, 100), text, fontsize=11)
    path = Path(tmp_path / "mixed.pdf")
    doc.save(path)
    (f,) = _rtl_findings(path, 400)
    assert (f.page, f.severity, f.extra["pages"]) == (2, "review", [2, 3]) and "Pages 2, 3" in f.detail
    assert _rtl_findings(path, 1) == []  # only the first pages are read
