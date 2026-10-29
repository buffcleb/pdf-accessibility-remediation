"""A multiple-choice question bank's questions are one list, each holding its choices (production job 8916bac5). An
equation or a small table between a question and its choices ended the list, so 67 questions came out as 28 lists,
and the choices as lists of their own beside them. Also: a question's bold second line and a small table's header row
were headings; an equation above choices set out in two columns was read between "c." and "d."; a subscript at the
foot of a choice went to the choice below; the AI could read a question's first choice before the question; a table
drawn with hairline rules was taken for a picture; and a charge set in smaller print, raised, split an equation."""
import pymupdf as fitz

from app.pipeline.enrichment import items_before_choices
from app.pipeline.orchestrator import prepare_analysis
from app.pipeline.structure.headings import assign_headings
from app.pipeline.structure.reading_order import heuristic_reading_order
from app.tagging.tagger import tag_document
from app.tagging.targets import PageResolver
from app.verification.tagmap import build_tagmap
from tests.engine.layout import block, document, page

FONT = dict(fontsize=10, fontname="tiro")


def _choices(p, y, letters="abcde", words=("one", "two", "three", "four", "five")):
    """Choices a. b. c. down the left, d. e. down the right, as a test bank sets them out."""
    for k, (letter, word) in enumerate(zip(letters, words)):
        x, row = (74, k) if k < 3 else (285, k - 3)
        p.insert_text((x, y + 12 * row), f"{letter}. Choice {word}", **FONT)


def _bank(path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((58, 70), "1. Balance the reaction below. How many electrons move from one side to the other?", **FONT)
    p.insert_text((240, 100), "Xa + Yb2 → XaYb", **FONT)  # the equation, centred, between question and choices
    _choices(p, 140)
    p.insert_text((58, 200), "2. Given the cell shown, if the potential of the copper electrode is known, what is the",
                  **FONT)
    p.insert_text((76, 212), "potential of the other electrode?", fontsize=10, fontname="tibo")  # bold, its 2nd line
    _choices(p, 240)
    p.insert_text((58, 300), "3. Which of the pairs in the small table below gives the largest cell potential?", **FONT)
    p.insert_text((180, 330), "Pair", fontsize=10, fontname="tibo")  # the table's header row, bold
    p.insert_text((320, 330), "Potential", fontsize=10, fontname="tibo")
    p.insert_text((180, 345), "first pair", **FONT)
    p.insert_text((320, 345), "0.42 V", **FONT)
    _choices(p, 380)
    doc.save(path)
    return path


def test_a_question_holds_its_equation_and_choices_and_the_questions_are_one_list(tmp_path):
    src = _bank(tmp_path / "bank.pdf")
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="Questions", language="en-US")
    els = build_tagmap(out)["elements"]
    by_i = {e["i"]: e for e in els}
    assert not [e for e in els if e["role"].startswith("H") and e["role"] != "H1"], "a heading inside the questions"
    lists = [e for e in els if e["role"] == "L"]
    top = [e for e in lists if by_i[e["parent"]]["role"] != "LBody"]
    assert len(top) == 1, [e["text"][:30] for e in top]
    questions = [by_i[k] for k in top[0]["kids"] if by_i[k]["role"] == "LI"]
    assert [q["text"][:2] for q in questions] == ["1.", "2.", "3."]
    for q in questions:
        (body,) = [by_i[k] for k in q["kids"] if by_i[k]["role"] == "LBody"]
        (choices,) = [by_i[k] for k in body["kids"] if isinstance(k, int) and by_i[k]["role"] == "L"]
        assert [by_i[k]["text"][:2] for k in choices["kids"]] == ["a.", "b.", "c.", "d.", "e."]
    first_body = next(by_i[k] for k in questions[0]["kids"] if by_i[k]["role"] == "LBody")
    kinds = [by_i[k]["role"] for k in first_body["kids"] if isinstance(k, int)]
    assert kinds.index("L") == len(kinds) - 1 and len(kinds) >= 2  # the equation, then the choices
    assert "potential of the other electrode" in questions[1]["text"]


def test_text_far_below_an_item_does_not_go_inside_it(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((58, 70), "1. The first question of the set, about a cell", **FONT)
    p.insert_text((120, 260), "A note set in, far below the question, about the next part.", **FONT)
    p.insert_text((58, 300), "2. The second question of the set, about a battery", **FONT)
    src = tmp_path / "far.pdf"
    doc.save(src)
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="Questions", language="en-US")
    els = build_tagmap(out)["elements"]
    by_i = {e["i"]: e for e in els}
    (note,) = [e for e in els if e["text"].startswith("A note set in")]
    assert by_i[note["parent"]]["role"] != "LBody"


def test_choices_beside_each_other_under_an_equation_read_after_it():
    q = block("q", (58, 60, 560, 72), "7. A question about the reaction below", role="LI", size=10, list_label="7.")
    eq = block("eq", (239, 90, 400, 102), "2A + 3B → 2C", role="Formula", size=10)
    a = block("a", (74, 140, 110, 151), "a. 3", role="LI", size=10, list_label="a.")
    b = block("b", (74, 152, 110, 163), "b. 6", role="LI", size=10, list_label="b.")
    c = block("c", (74, 164, 110, 175), "c. 8", role="LI", size=10, list_label="c.")
    d = block("d", (285, 140, 320, 151), "d. 12", role="LI", size=10, list_label="d.")
    e = block("e", (285, 152, 320, 163), "e. 2", role="LI", size=10, list_label="e.")
    order = heuristic_reading_order(page(q, eq, a, b, c, d, e))
    assert order.index("eq") < order.index("a") and order.index("eq") < min(order.index("c"), order.index("d"))
    # ...and with a small table's header to the right above them, not only a centred equation
    head = block("h", (364, 100, 480, 110), "Potential", size=10)
    order = heuristic_reading_order(page(q, eq, head, a, b, c, d, e))
    assert max(order.index("eq"), order.index("h")) < min(order.index(x) for x in "abcde")


def test_a_bold_line_between_a_question_and_its_choices_is_not_a_heading():
    blocks = [block("q", (58, 41, 560, 53), "28. Given the reaction shown, if the", size=10),
              block("cont", (76, 51, 330, 64), "potential of the other electrode?", size=10, bold=True),
              block("a", (80, 83, 140, 94), "a. +0.85 V", size=10), block("b", (80, 94, 140, 105), "b. -0.85 V", size=10)]
    doc = document(page(*blocks), body=10.0)
    assign_headings(doc)
    assert blocks[1].role == "P"
    # A bold line at the margin, with nothing labelled under it, is still a heading
    alone = [block("h", (58, 300, 300, 312), "Cell potentials", size=10, bold=True),
             block("p", (58, 320, 560, 360), "Some text about cells that runs on for a while.", size=10)]
    doc = document(page(*alone), body=10.0)
    assign_headings(doc)
    assert alone[0].role.startswith("H")


def test_a_subscript_reaching_into_the_choice_below_stays_with_its_own():
    a = block("a", (74, 685.4, 300, 699.0), "a. X2Y4 was reduced", role="LI", size=10, list_label="a.")
    b = block("b", (74, 696.6, 300, 710.0), "b. X2Y4 was oxidized", role="LI", size=10, list_label="b.")
    resolver = PageResolver(page(a, b))
    assert resolver("text", (178.3, 691.4, 181.5, 698.6), 0)[0] == "a"  # the "2" of choice a.
    assert resolver("text", (175.5, 702.9, 178.8, 710.1), 0)[0] == "b"  # the "2" of choice b.
    # A power at the top of a line, reaching into the line above, stays on its own line
    above = block("up", (127, 516, 490, 596), "A paragraph above", size=10)
    line = block("line", (127, 586, 490, 609), "C-1, 0.000. and so on", size=10)
    assert PageResolver(page(above, line))("text", (133.9, 583.2, 137.3, 588.2), 0)[0] == "line"
    # ...and so does a run of a line's own text, in its own print, that a paragraph's box above overlaps
    para = block("para", (127, 118, 490, 649), "A long paragraph", size=10)
    nxt = block("next", (127, 628, 490, 699), "The next paragraph", size=10)
    assert PageResolver(page(para, nxt))("text", (126.7, 622.4, 422.0, 633.3), 0)[0] == "next"


def test_the_ai_cannot_read_a_choice_before_its_question():
    q = block("q17", (58, 100, 560, 112), "17. A question", role="LI", size=10, list_label="17.")
    a = block("a", (74, 120, 300, 131), "a. A choice", role="LI", size=10, list_label="a.")
    b = block("b", (74, 132, 300, 143), "b. Another", role="LI", size=10, list_label="b.")
    q18 = block("q18", (58, 160, 560, 172), "18. The next question", role="LI", size=10, list_label="18.")
    a18 = block("a18", (74, 180, 300, 191), "a. Its choice", role="LI", size=10, list_label="a.")
    p = page(q, a, b, q18, a18)
    assert items_before_choices(p, ["a", "q17", "b", "q18", "a18"]) == (["q17", "a", "b", "q18", "a18"], 1)
    assert items_before_choices(p, ["q17", "a", "b", "q18", "a18"])[1] == 0
    # The next question's choices aren't the first question's own
    assert items_before_choices(p, ["q17", "a", "b", "a18", "q18"]) == (["q17", "a", "b", "q18", "a18"], 1)


def _ruled_box(p, lines, x0=52, x1=490, top=97, bottom=543):
    """A one-row, two-cell table whose borders are hairline filled boxes, as Word draws them, a column of short lines
    in each cell (an answer key)."""
    mid = (x0 + x1) / 2
    for y in (top, bottom):
        p.draw_rect(fitz.Rect(x0, y, x1, y + 0.5), color=None, fill=(0, 0, 0))
    for x in (x0, mid, x1):
        p.draw_rect(fitz.Rect(x, top, x + 0.5, bottom), color=None, fill=(0, 0, 0))
    for r, (left, right) in enumerate(lines):
        p.insert_text((x0 + 6, top + 16 + 13 * r), left, **FONT)
        p.insert_text((mid + 6, top + 16 + 13 * r), right, **FONT)


def test_a_table_drawn_in_hairline_rules_is_not_a_picture(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    _ruled_box(p, [(f"{n}. A", f"{n + 30}. C") for n in range(1, 31)])
    src = tmp_path / "key.pdf"
    doc.save(src)
    assert not [i for pa in prepare_analysis(src).pages for i in pa.images if i.kind == "vector"]


def test_a_raised_charge_starting_a_text_object_stays_in_its_equation(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((176, 355), "Pq(OH)4", **FONT)
    w = fitz.get_text_length("Pq(OH)4", fontname="tiro", fontsize=10)
    p.insert_text((176 + w, 349), "2-", fontsize=6.5, fontname="tiro")
    p.insert_text((176 + w + 8, 355), "+ ZO- → PqO2 + Z- + OH-", **FONT)
    src = tmp_path / "eq.pdf"
    doc.save(src)
    texts = [b.text for b in prepare_analysis(src).pages[0].blocks if b.role != "Artifact"]
    assert len(texts) == 1, texts


def test_a_diagram_beside_the_last_question_holds_its_labels_and_goes_in_the_question(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((58, 600), "22. For the cell shown, which metal is the cathode and which is the anode?", **FONT)
    for k, letter in enumerate("abcde"):
        p.insert_text((80, 640 + 10 * k), f"{letter}. Choice {letter}", **FONT)
    picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 30), False)
    picture.set_rect(picture.irect, (200, 220, 240))
    p.insert_image(fitz.Rect(300, 620, 450, 700), pixmap=picture)
    for x, y, label in ((350, 632, "meter"), (395, 650, "wire"), (310, 690, "metal one")):
        p.insert_text((x, y), label, fontsize=4, fontname="tiro")
    src = tmp_path / "cell.pdf"
    doc.save(src)
    analysis = prepare_analysis(src)
    (img,) = analysis.pages[0].images
    assert img.labels == "meter wire metal one"
    out = tmp_path / "out.pdf"
    tag_document(src, out, analysis, title="Questions", language="en-US")
    els = build_tagmap(out)["elements"]
    by_i = {e["i"]: e for e in els}
    (fig,) = [e for e in els if e["role"] == "Figure"]
    body = by_i[fig["parent"]]
    assert body["role"] == "LBody" and by_i[body["parent"]]["text"].startswith("22.")
    assert not [e for e in els if e["role"] == "P" and e["text"] in ("meter", "wire", "metal one")]
