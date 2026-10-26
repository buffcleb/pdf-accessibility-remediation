"""#599: a worksheet's centred title over its "Part 1: ..." line, both bold, set as one block by the producer: two
headings, Part 1 at Part 2's level. A section's label ("Part 1:") makes a heading of a line ending in a full stop."""
import pymupdf as fitz

from app.pipeline.orchestrator import prepare_analysis
from app.pipeline.models import TextBlock
from app.pipeline.structure.headings import is_heading_candidate

PART1 = "Part 1:  A few common rate problems often seen on the quizzes."
TITLE = "CH210 Worksheet 3:  Reaction Rates"


def _worksheet(tmp_path, title_x=None, bold=True):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    font = "tibo" if bold else "tiro"
    w = fitz.get_text_length(TITLE, font, 12)
    # One text block, as the producer wrote it: the title centred, Part 1 at the margin under it
    tw = fitz.TextWriter(page.rect)
    tw.append(((612 - w) / 2 if title_x is None else title_x, 50), TITLE, font=fitz.Font(font), fontsize=12)
    tw.append((36, 64), PART1, font=fitz.Font(font), fontsize=12)
    tw.write_text(page)
    y = 100
    for n in range(8):
        page.insert_text((36, y), "Body text of the worksheet that explains the problems in ordinary print.",
                         fontsize=10, fontname="tiro")
        y += 13
    page.insert_text((36, y + 20), "Part 2:  A General Way to Work Through Reaction Rate Problems", fontsize=12,
                     fontname="tibo")
    for n in range(8):
        page.insert_text((36, y + 40 + 13 * n), "More body text, in the same ordinary print as the rest of it.",
                         fontsize=10, fontname="tiro")
    src = tmp_path / "worksheet.pdf"
    doc.save(src)
    return src


def _heads(src):
    return [(b.role, b.text[:7]) for p in prepare_analysis(src).pages for b in p.blocks if b.role.startswith("H")
            or b.text.startswith(("CH210", "Part"))]


def test_a_centred_title_over_part_1_is_two_headings_at_part_2s_level(tmp_path):
    assert _heads(_worksheet(tmp_path)) == [("H1", "CH210 W"), ("H1", "Part 1:"), ("H1", "Part 2:")]


def test_a_line_starting_at_the_margin_stays_with_the_line_above_it(tmp_path):
    # The first line starts at the margin too: one paragraph, as before (a heading it isn't: it ends in a full stop
    # and Part 1's label is in its middle)
    heads = _heads(_worksheet(tmp_path, title_x=36))
    assert heads[0] == ("P", "CH210 W") and ("H1", "Part 2:") in heads and len(heads) == 2


def test_a_section_label_makes_a_heading_of_a_line_ending_in_a_full_stop():
    def block(text):
        return TextBlock(id="b", page=0, bbox=(0, 0, 1, 1), text=text, font_size=12, bold=True)
    assert is_heading_candidate(block(PART1), 10)
    assert is_heading_candidate(block("Chapter 3. What the survey found across all of the forty sites."), 10)
    assert not is_heading_candidate(block("A few common rate problems often seen on the quizzes here."), 10)
