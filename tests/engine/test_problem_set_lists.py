"""A problem set's numbered problems are one list (production job 9d0e9614): a graph between problems 6 and 7 and the
page break between 8 and 9 split twelve problems into four lists. A graph between two items of a list belongs to the
item before it ("6. The graph below ..."), and a list that ends a page and goes on at the top of the next is one.
Also the checker's text: TeX's justified lines set word spaces down to about 0.23 em, read as no space at all."""
import pymupdf as fitz

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.semantic import glyph_text
from app.verification.tagmap import build_tagmap


def _problem_set(path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    y = 90
    for n in range(1, 4):
        p.insert_text((72, y), f"{n}.", fontsize=11, fontname="tiro")
        p.insert_text((96, y), f"Problem number {n} asks about the motion of a cart on a level track.", fontsize=11,
                      fontname="tiro")
        y += 30
    p.draw_rect(fitz.Rect(220, 200, 420, 300), color=(0, 0, 0), width=0.8)  # the graph problem 3 speaks of
    for k, h in enumerate((30, 55, 70, 45, 60, 20)):
        p.draw_rect(fitz.Rect(225 + 32 * k, 300 - h, 245 + 32 * k, 300), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    y = 340
    for n in range(4, 6):
        p.insert_text((72, y), f"{n}.", fontsize=11, fontname="tiro")
        p.insert_text((96, y), f"Problem number {n} asks about the motion of a cart on a level track.", fontsize=11,
                      fontname="tiro")
        y += 30
    p = doc.new_page(width=612, height=792)
    for n, y in ((6, 90), (7, 120)):
        p.insert_text((72, y), f"{n}.", fontsize=11, fontname="tiro")
        p.insert_text((96, y), f"Problem number {n} asks about the motion of a cart on a level track.", fontsize=11,
                      fontname="tiro")
    doc.save(path)
    return path


def test_a_graph_between_problems_and_a_page_break_leave_one_list(tmp_path):
    src = _problem_set(tmp_path / "ps.pdf")
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="Problems", language="en-US")
    els = build_tagmap(out)["elements"]
    lists = [e for e in els if e["role"] == "L"]
    assert len(lists) == 1, [(e["page"], e["text"][:30]) for e in lists]
    items = [e for e in els if e["role"] == "LI"]
    assert [e["text"].split(".")[0] for e in items] == [str(n) for n in range(1, 8)]
    (fig,) = [e for e in els if e["role"] == "Figure"]
    third = next(e for e in items if e["text"].startswith("3."))
    assert fig["parent"] in third["kids"]  # inside problem 3's LBody


def test_a_list_after_a_paragraph_stays_its_own(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 90), "1.", fontsize=11, fontname="tiro")
    p.insert_text((96, 90), "The first problem of the set, about a cart on a track.", fontsize=11, fontname="tiro")
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 90), "A paragraph of instructions that comes before the next problems on this page.",
                  fontsize=11, fontname="tiro")
    p.insert_text((72, 130), "2.", fontsize=11, fontname="tiro")
    p.insert_text((96, 130), "The second problem of the set, about a ball on a slope.", fontsize=11, fontname="tiro")
    src = tmp_path / "two.pdf"
    doc.save(src)
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="Problems", language="en-US")
    assert len([e for e in build_tagmap(out)["elements"] if e["role"] == "L"]) == 2


def test_a_justified_lines_narrow_word_spaces_are_spaces():
    chars, x = [], 72.0
    for word in ("A", "train", "is", "initially", "at", "rest"):
        for ch in word:
            chars.append({"text": ch, "size": 12.0, "x0": x, "x1": x + 5, "top": 100.0})
            x += 5
        x += 2.8  # 0.23 em
    assert glyph_text(chars)[0] == "A train is initially at rest"
    kerned = [{"text": ch, "size": 12.0, "x0": 72 + 5.3 * k, "x1": 77 + 5.3 * k, "top": 100.0}
              for k, ch in enumerate("room")]  # letters 0.025 em apart
    assert glyph_text(kerned)[0] == "room"


def test_a_heading_set_with_wide_letter_spacing_stays_words():
    chars, x = [], 72.0
    for word in ("NON-LINEAR", "EFFECTS"):
        for ch in word:
            chars.append({"text": ch, "size": 12.0, "x0": x, "x1": x + 7, "top": 100.0})
            x += 7 + 2.6  # every letter 0.22 em apart
        x += 6.0  # the word space, wider
    assert glyph_text(chars)[0] == "NON-LINEAR EFFECTS"


def test_a_line_that_writes_its_spaces_is_not_split_at_a_wide_letter():
    """An OCR layer's words are apart by space characters, its glyphs narrower than the picture's ("J ohn")."""
    chars, x = [], 72.0
    for n, word in enumerate(("contact", "John", "Matthews")):
        if n:
            chars.append({"text": " ", "size": 10.0, "x0": x, "x1": x + 3, "top": 100.0})
            x += 3
        for ch in word:
            chars.append({"text": ch, "size": 10.0, "x0": x, "x1": x + 5, "top": 100.0})
            x += 5 + (2.1 if ch == "J" else 0.0)  # 0.21 em after the J
    assert glyph_text(chars)[0] == "contact John Matthews"
