"""A rule with text over and under it that isn't a fraction: the underline of a link that wraps onto a second line, a
table cell's border between two cells, a rule between two lines of prose. Found on an accessibility plan whose web
addresses were read "fraction, broome.edu/online, over /faculty-online/), end fraction", and on five more documents."""
import pymupdf as fitz

from app.pipeline.analyzer import analyze_page


def _spoken(tmp_path, draw):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    draw(page)
    doc.save(tmp_path / "p.pdf")
    pa, _ = analyze_page(fitz.open(tmp_path / "p.pdf"), 0)
    return [(b.text, b.actual_text) for b in pa.blocks if b.role != "Artifact"]


def _fractions(tmp_path, draw):
    return [(t, a) for t, a in _spoken(tmp_path, draw) if a and " over " in a]


def _underlined(p, x, base, text, size=10):
    p.insert_text((x, base), text, fontsize=size, fontname="helv")
    p.draw_line((x, base + 1.4), (x + fitz.get_text_length(text, "helv", size), base + 1.4), width=0.5)


def test_a_wrapped_underlined_link_is_not_a_fraction(tmp_path):
    def draw(p):
        _underlined(p, 300, 100, "(http://www3.example")
        _underlined(p, 300, 112, "college.edu/online")
        _underlined(p, 300, 124, "/faculty-online/)")
        # Shorter lines, in a narrow cell
        _underlined(p, 480, 300, "2010 ADA", 9)
        _underlined(p, 480, 311, "Standard", 9)
    assert _fractions(tmp_path, draw) == []


def test_a_cell_border_between_two_cells_is_not_a_fraction(tmp_path):
    def draw(p):
        # Two columns of centered cells: each border is drawn cell by cell, and upright rules meet them
        for x0, x1, top, bottom in ((100, 160, "4", "5"), (160, 260, "Final", "20%")):
            for y in (180, 200, 220):
                p.draw_line((x0, y), (x1, y), width=0.5)
            for text, base in ((top, 194), (bottom, 214)):
                w = fitz.get_text_length(text, "helv", 10)
                p.insert_text(((x0 + x1 - w) / 2, base), text, fontsize=10, fontname="helv")
        for x in (100, 160, 260):
            p.draw_line((x, 180), (x, 220), width=0.5)
    assert _fractions(tmp_path, draw) == []


def test_a_box_drawn_as_a_rectangle_is_not_a_fraction(tmp_path):
    def draw(p):
        p.draw_rect(fitz.Rect(100, 200, 140, 216), color=(0, 0, 0), width=0.5)
        p.insert_text((117, 196), "7", fontsize=10, fontname="helv")   # just above the box's top side
        p.insert_text((117, 211), "3", fontsize=10, fontname="helv")   # inside it
    assert _fractions(tmp_path, draw) == []


def test_a_rule_between_two_lines_of_prose_is_not_a_fraction(tmp_path):
    def draw(p):
        p.insert_text((104, 195), "on the reader role.", fontsize=10, fontname="helv")
        p.draw_line((100, 199), (187, 199), width=0.5)
        p.insert_text((104, 211), "March 2019", fontsize=10, fontname="helv")
        # A sentence that mentions a number is still a sentence
        p.insert_text((104, 395), "pages with the new monthly summaries", fontsize=10, fontname="helv")
        p.draw_line((100, 399), (330, 399), width=0.5)
        p.insert_text((104, 411), "everyone will get a copy of summary 103", fontsize=10, fontname="helv")
    assert _fractions(tmp_path, draw) == []


def test_words_over_words_beside_an_equals_sign_are_still_a_fraction(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "slope =", fontsize=11, fontname="tiro")
        p.insert_text((146, 192), "rise", fontsize=11, fontname="tiro")
        p.draw_line((142, 196), (166, 196), width=0.5)
        p.insert_text((146, 207), "run", fontsize=11, fontname="tiro")
    found = _fractions(tmp_path, draw)
    assert len(found) == 1 and found[0][1] == "slope = rise over run", found


def test_a_fraction_whose_numerator_is_as_wide_as_its_bar_is_still_a_fraction(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "x =", fontsize=11, fontname="tiro")
        w = fitz.get_text_length("2a + b", "tiro", 11)
        p.insert_text((126, 192), "2a + b", fontsize=11, fontname="tiro")
        p.draw_line((126, 196), (126 + w, 196), width=0.5)  # exactly as wide as the numerator
        p.insert_text((136, 207), "365", fontsize=11, fontname="tiro")
    found = _fractions(tmp_path, draw)
    assert len(found) == 1 and found[0][1] == "x = fraction, 2a + b, over 365, end fraction", found
