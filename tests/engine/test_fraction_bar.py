"""A stacked fraction's "over" is a line the page draws, not a character: the division check accepts a reading that says
it when the region draws a fraction bar, and only then. Other lines between two lines of text aren't fraction bars."""
import pymupdf as fitz

from app.pipeline.notation import DIVISION_NOT_WRITTEN, drawn_fraction, rejection_reason, spoken_consistent


def _page(parts, line, size=11):
    """parts: (text, x, baseline y, font size); line: (x0, x1, y, width)."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for text, x, y, sz in parts:
        page.insert_text((x, y), text, fontsize=sz, fontname="helv")
    x0, x1, y, w = line
    page.draw_line((x0, y), (x1, y), width=w)
    return doc, page


def _bbox(page):
    words = page.get_text("words")
    return (min(w[0] for w in words), min(w[1] for w in words) - 2, max(w[2] for w in words), max(w[3] for w in words) + 2)


def _centered(text, size, cx):
    return cx - fitz.get_text_length(text, "helv", size) / 2


def test_a_stacked_fraction_draws_a_bar():
    for top, bottom in (("a + b", "c"), ("1", "2"), ("dy", "dx"), ("rise", "run")):
        w = max(fitz.get_text_length(t, "helv", 11) for t in (top, bottom)) + 4
        doc, page = _page([(top, _centered(top, 11, 300), 100, 11), (bottom, _centered(bottom, 11, 300), 116, 11)],
                          (300 - w / 2, 300 + w / 2, 104, 0.6))
        assert drawn_fraction(page, _bbox(page), 11), (top, bottom)


def test_what_isnt_a_fraction_bar():
    cases = {
        # A name, a rule across the column, its tagline in smaller type
        "name, long rule, tagline": ([("Morgan Homes", _centered("Morgan Homes", 18, 306), 100, 18),
                                      ("Putting a roof over your head for 50 years",
                                       _centered("Putting a roof over your head for 50 years", 12, 306), 122, 12)],
                                     (72, 540, 106, 1.0)),
        # ...the rule only as long as the name: the tagline sticks out past it, and is smaller
        "name, short rule, tagline": ([("Morgan Homes", _centered("Morgan Homes", 18, 306), 100, 18),
                                       ("Putting a roof over your head for 50 years",
                                        _centered("Putting a roof over your head for 50 years", 12, 306), 122, 12)],
                                      (306 - 58, 306 + 58, 106, 1.0)),
        # Same size, short rule, centered: shaped like a fraction, but both parts are phrases
        "name over a phrase, same size": ([("Morgan Homes", _centered("Morgan Homes", 11, 306), 100, 11),
                                           ("Since the year 1975", _centered("Since the year 1975", 11, 306), 116, 11)],
                                          (306 - 50, 306 + 50, 104, 0.6)),
        # A heading's underline, the paragraph well below it
        "heading underline": ([("Results", 72, 100, 14), ("The survey covered forty sites.", 72, 140, 11)],
                              (72, 125, 103, 0.8)),
        # A table's rule under its header row, across the table
        "table rule": ([("Drug", 80, 100, 11), ("Dose", 300, 100, 11), ("Amoxicillin", 80, 116, 11), ("500", 300, 116, 11)],
                       (72, 400, 104, 0.6)),
        # A line through the text (strike-through)
        "strike-through": ([("x + 1", 290, 104, 11)], (288, 320, 100, 0.6)),
    }
    for name, (parts, line) in cases.items():
        doc, page = _page(parts, line)
        assert not drawn_fraction(page, _bbox(page), 11), name


def test_the_division_check_takes_the_bar_into_account():
    page_text, said = "a + b c", "a plus b over c"
    assert spoken_consistent(page_text, said) == (False, DIVISION_NOT_WRITTEN)
    assert spoken_consistent(page_text, said, fraction_drawn=True) == (True, "")
    # Every other check still applies
    assert spoken_consistent("a + 2 c", "a plus b over c", fraction_drawn=True)[0] is False


def test_rejections_are_counted_by_reason():
    assert rejection_reason("the numbers 2.5, 10 on the page are not in the AI's spoken reading") == \
        "the numbers N, N on the page are not in the AI's spoken reading"
    assert rejection_reason("3 of 40 characters on the page are missing from the AI's reading") == \
        "N of N characters on the page are missing from the AI's reading"


def test_a_bar_drawn_as_a_thin_picture_counts():
    """Some TeX converters draw each rule as a picture one pixel tall, stretched: a 531-page book drew every fraction
    bar that way, and no line or rectangle at all."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((_centered("s + t", 11, 300), 100), "s + t", fontsize=11, fontname="helv")
    page.insert_text((_centered("m + 1", 11, 300), 116), "m + 1", fontsize=11, fontname="helv")
    dot = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1, 1), False)
    dot.clear_with(0)
    page.insert_image(fitz.Rect(284, 103.8, 316, 104.3), pixmap=dot, keep_proportion=False)
    assert page.get_drawings() == []
    assert drawn_fraction(page, _bbox(page), 11)


def test_lines_that_belong_to_something_else_arent_fraction_bars():
    """Measured on the regression set: a reaction arrow with its reagents above and below, a table's header rule, a
    drawing's dimension label (inches over millimetres)."""
    # A reaction arrow: a line with an arrowhead at its end, a reagent above and one below
    doc, page = _page([("NaCN", _centered("NaCN", 9, 300), 100, 9), ("H2SO4", _centered("H2SO4", 9, 300), 114, 9)],
                      (280, 320, 104, 0.6))
    page.draw_polyline([(316, 101), (322, 104), (316, 107)], fill=(0, 0, 0))
    assert not drawn_fraction(page, _bbox(page), 9)
    # A table: a header over a row, ruled above and below with rules of the same length
    doc, page = _page([("PIN", 290, 100, 9), ("1", 296, 114, 9)], (282, 318, 104, 0.6))
    page.draw_line((282, 88), (318, 88), width=0.6)
    page.draw_line((282, 120), (318, 120), width=0.6)
    assert not drawn_fraction(page, _bbox(page), 9)
    # A dimension label: the line between inches and millimetres runs on to extension lines
    doc, page = _page([(".016", _centered(".016", 8, 300), 100, 8), ("(0.406)", _centered("(0.406)", 8, 300), 113, 8)],
                      (282, 318, 104, 0.4))
    page.draw_line((282, 98), (282, 110), width=0.4)
    page.draw_line((318, 98), (318, 110), width=0.4)
    assert not drawn_fraction(page, _bbox(page), 8)


def test_a_dimension_over_its_millimetres_isnt_a_fraction():
    """A datasheet's drawing labels each size in inches over the same in millimetres, a short line between: shaped like
    a fraction, but a stacked fraction never puts its whole denominator in parentheses."""
    top, bottom = ".016 - .050", "(0.406 - 1.270)"
    w = fitz.get_text_length(bottom, "helv", 8) + 2
    doc, page = _page([(top, _centered(top, 8, 300), 100, 8), (bottom, _centered(bottom, 8, 300), 112, 8)],
                      (300 - w / 2, 300 + w / 2, 103, 0.4))
    assert not drawn_fraction(page, _bbox(page), 8)
    # A parenthesis inside the denominator is still a fraction
    top, bottom = "1", "(k!)2"
    doc, page = _page([(top, _centered(top, 11, 300), 100, 11), (bottom, _centered(bottom, 11, 300), 116, 11)],
                      (288, 312, 104, 0.6))
    assert drawn_fraction(page, _bbox(page), 11)
