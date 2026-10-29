"""The font size of an OCR line. A heading set over several lines came apart where one line had descenders and the
others didn't (its first line became the document's title), and the stars of a rating read as letters made a heading
of their line."""
import pytest

from app.pipeline.ocr import _line_size


def _row(text, top, bottom, conf=96.0):
    return {"text": text, "conf": conf, "line": (0.0, top, 50.0, bottom)}


def test_lines_of_one_heading_get_one_size_with_or_without_descenders():
    plain = _line_size([_row("Western", 100, 111), _row("Shore", 100, 111)])      # nothing below the baseline
    hanging = _line_size([_row("Library", 100, 113.4)])                           # y reaches below it
    assert plain == pytest.approx(hanging, rel=0.05)


def test_a_mark_read_with_no_confidence_doesnt_set_the_size():
    alone = _line_size([_row("Rate", 103, 110), _row("this", 103, 110)])
    with_stars = _line_size([_row("xkxkxkXe", 100, 114, conf=5.0), _row("Rate", 103, 110), _row("this", 103, 110)])
    assert with_stars == alone
    # Nothing sure on the line: its words are all there is to go by
    assert _line_size([_row("xkxk", 100, 114, conf=5.0)]) > alone
