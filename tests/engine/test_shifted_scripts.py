"""Scripts typeset at full size and moved off the line (a text rise), and numbered or lettered list items in larger
print than the body. A chemistry worksheet set "N" then "2(g)" lowered a third of a character at the line's own size:
the lowered part became a line of its own, split the formula into a heading, a list item and a paragraph, and every
numbered question on its first page (12 pt, against a 10 pt body) became a heading."""
from app.pipeline.analyzer import _Run, _visual_lines
from app.pipeline.models import TextBlock
from app.pipeline.structure.headings import is_heading_candidate


def _runs(chars: list[tuple[str, float, float]], size: float = 12.0) -> list[_Run]:
    """(text, x, baseline) per character, all at one size."""
    out = []
    for seq, (c, x, base) in enumerate(chars):
        char = {"bbox": (x, base - 0.8 * size, x + 0.5 * size, base + 0.2 * size), "c": c, "origin": (x, base)}
        out.append(_Run({"size": size, "flags": 0, "font": "TimesNewRomanPSMT", "color": 0}, char, seq))
    return out


def _line(text: str, x: float, base: float, step: float = 6.0) -> list[tuple[str, float, float]]:
    return [(c, x + step * i, base) for i, c in enumerate(text)]


def test_full_size_text_lowered_off_the_line_is_its_subscript():
    # "(b) N" "(g)" "+ O" "2(g)" "↔ 2NO" "(g)": the parts in brackets lowered 4.1 points at 12 points, each
    # starting where the character before it ends
    chars = (_line("(b)N", 72, 129.8) + _line("(g)", 96, 133.9) + _line("+O", 125, 129.8)
             + _line("2(g)", 137, 133.9) + _line("↔2NO", 172, 129.8) + _line("(g)", 196, 133.9))
    lines = _visual_lines(_runs(chars))
    assert len(lines) == 1
    assert "".join(r.text for r in lines[0].runs) == "(b)N(g)+O2(g)↔2NO(g)"
    scripts = "".join(r.text for r in lines[0].runs if r.script == "sub")
    assert scripts == "(g)2(g)(g)"


def test_full_size_text_raised_off_the_line_is_its_superscript():
    lines = _visual_lines(_runs(_line("x", 72, 100.0) + _line("2", 78, 95.5) + _line("+y", 90, 100.0)))
    assert len(lines) == 1 and [r.script for r in lines[0].runs] == [None, "sup", None, None]


def test_lines_a_whole_line_apart_stay_apart():
    lines = _visual_lines(_runs(_line("first", 72, 100.0) + _line("second", 72, 114.0)))
    assert [("".join(r.text for r in ln.runs)) for ln in lines] == ["first", "second"]


def test_text_beside_a_line_and_off_its_baseline_is_not_taken_for_a_script():
    # Two cells of a row, vertically centred differently: far apart, so not one line
    lines = _visual_lines(_runs(_line("Total", 72, 100.0) + _line("42", 300, 104.0)))
    assert len(lines) == 2


def _block(text: str, size: float = 12.0, bold: bool = False) -> TextBlock:
    return TextBlock(id="b", page=0, bbox=(36, 0, 500, 14), text=text, font_size=size, bold=bold)


def test_list_items_in_larger_print_are_not_headings():
    body = 10.0
    for text in ["(a) H2(g) + O2(g) ↔ H2O(g)", "(iv) the fourth", "b) a lettered item",
                 "1. Gas laws and volume: If the container is made smaller, what happens to the pressure inside?",
                 "3. Gas laws and temperature:",
                 "4. Linking Unit 3 to Unit 6: What is the rate constant at body temperature for the step A+ + B-"]:
        assert not is_heading_candidate(_block(text, bold=text.startswith("3.")), body), text


def test_numbered_headings_in_larger_print_still_are():
    body = 10.0
    for text in ["1. Introduction", "2.1 Cost (in $) – a summary", "A. Methods", "Part 2: General Approach"]:
        assert is_heading_candidate(_block(text), body), text


def test_a_footnote_mark_a_point_smaller_stays_its_own_line():
    # "10" at 8 pt raised beside the note's 9 pt text: the footnote pass joins them, as before
    mark = _runs(_line("10", 144.6, 638.9, 4.2), size=7.97)
    text = _runs(_line("Note", 153.1, 642.4, 4.8), size=8.97)
    for n, r in enumerate(mark + text):
        r.seq = n
    assert len(_visual_lines(mark + text)) == 2


def test_a_short_shifted_start_is_the_lines_script():
    # Same size, raised, at the start of a longer line: the line keeps its own baseline
    lines = _visual_lines(_runs(_line("2", 72, 95.5) + _line("Noted", 78, 100.0) + _line("here", 114, 100.0)))
    assert len(lines) == 1 and lines[0].base == 100.0
    assert [r.script for r in lines[0].runs if r.text == "2"] == ["sup"]


def test_labels_above_and_below_a_diagrams_arrow_stay_apart():
    # "RX" then a reagent written above the arrow, a space away, at the same size: not a superscript of "RX"
    lines = _visual_lines(_runs(_line("RX", 72, 100.0) + _line("CN", 110, 95.5) + _line("RCN", 150, 100.0)))
    assert len(lines) == 2
