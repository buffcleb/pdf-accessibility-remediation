"""Notation on the Adobe route (#20, part 3): readings of specialist notation, and doses put in words by rule, reach
the improved copy as ActualText on Adobe's text tags. Adobe's own file stays as it was (see test_adobe.py for the
whole route)."""
from app.pipeline.adobe_job import notation_edits
from app.pipeline.models import TableCell, TableRegion
from tests.engine.layout import block, document, page

LINE = "The second batch held more H2O2 once it was warmed."
SAID = "The second batch held more H 2 O 2 once it was warmed."


def _analysis(blocks, tables=()):
    return document(page(*blocks, *tables))


def _read(bbox=(72, 88, 538, 104), text=LINE, said=SAID, role="P"):
    return block("b", bbox, text, role, actual_text=said, notation_reading=True)


def _el(i, role, bbox=(72, 91, 520, 101), page=1, text=LINE, **kw):
    return {"i": i, "role": role, "boxes": [{"page": page, "bbox": list(bbox)}], "text": text, **kw}


def test_a_reading_goes_onto_the_tag_that_holds_its_text():
    # The tag's box hugs the glyphs, the block's spans the line: the text says they're the same
    tagmap = {"elements": [_el(3, "P"), _el(4, "P", (72, 131, 520, 141), text="Another line.")]}
    edits, unplaced = notation_edits(_analysis([_read()]), tagmap)
    assert edits == [{"op": "set_actual_text", "element": 3, "expect_role": "P", "text": SAID,
                      "bbox": [72, 88, 538, 104]}]
    assert unplaced == 0
    # Spacing and line-end hyphens don't count
    assert notation_edits(_analysis([_read()]), {"elements": [_el(3, "P", text=LINE.replace("once", "on-\nce"))]})[0]


def test_tags_that_cant_take_a_reading_are_left_alone():
    reading = _analysis([_read()])
    for el in (_el(3, "P", alt="Hydrogen peroxide"),          # says something of its own already
               _el(3, "P", actual_text="H two O two"),
               _el(3, "P", annot=True),                        # holds a link or field
               _el(3, "Figure"),                               # not a text tag
               _el(3, "P", page=2),                            # another page
               _el(3, "P", (72, 300, 520, 310))):              # the same words somewhere else on the page
        assert notation_edits(reading, {"elements": [el]}) == ([], 1), el
    # A tag holding more than plain Spans (a Link, say) would lose it to the ActualText
    link = {"elements": [_el(3, "P", kids=[4]), _el(4, "Link", (300, 91, 340, 101), text="H2O2")]}
    assert notation_edits(reading, link) == ([], 1)
    spans = {"elements": [_el(3, "P", kids=[4]), _el(4, "Span", (300, 91, 340, 101), text="H2O2")]}
    assert [e["element"] for e in notation_edits(reading, spans)[0]] == [3]


def test_a_tag_that_divides_the_text_differently_gets_no_reading():
    # Adobe made one paragraph of this line and the next
    tagmap = {"elements": [_el(3, "P", (72, 91, 520, 141), text=LINE + " It was then left to cool.")]}
    assert notation_edits(_analysis([_read()]), tagmap) == ([], 1)


def test_a_list_item_takes_the_reading_of_its_label_and_body():
    text, said = "2. Give 5 mg/kg PO q6h.", "2. Give 5 milligrams per kilogram by mouth every 6 hours."
    tagmap = {"elements": [_el(3, "LI", text=text, kids=[4, 5]), _el(4, "Lbl", (72, 91, 84, 101), text="2."),
                           _el(5, "LBody", (88, 91, 300, 101), text="Give 5 mg/kg PO q6h.")]}
    edits, _ = notation_edits(_analysis([_read(text=text, said=said, role="LI")]), tagmap)
    assert [(e["element"], e["expect_role"]) for e in edits] == [(3, "LI")]


def test_doses_in_table_cells_reach_the_cells():
    cell = TableCell(row=1, col=1, text="5 mg/kg", bbox=(300, 200, 360, 214), actual_text="5 milligrams per kilogram")
    table = TableRegion(id="t1", page=0, bbox=(70, 180, 540, 260), n_rows=3, n_cols=2, cells=[cell])
    tagmap = {"elements": [_el(9, "TD", (302, 203, 340, 212), text="5 mg/kg")]}
    edits, _ = notation_edits(_analysis([], [table]), tagmap)
    assert [(e["element"], e["expect_role"], e["text"]) for e in edits] == [(9, "TD", "5 milligrams per kilogram")]


def test_a_cell_whose_lines_come_out_in_another_order_is_still_found():
    cell = TableCell(row=0, col=1, text="Dose 5 mg/kg", bbox=(300, 200, 360, 228),
                     actual_text="Dose 5 milligrams per kilogram")
    table = TableRegion(id="t1", page=0, bbox=(70, 180, 540, 260), n_rows=3, n_cols=2, cells=[cell])
    tagmap = {"elements": [_el(9, "TH", (302, 203, 350, 226), text="5 mg/kg Dose")]}
    assert [e["element"] for e in notation_edits(_analysis([], [table]), tagmap)[0]] == [9]
    # Other characters are another cell's text
    other = {"elements": [_el(9, "TH", (302, 203, 350, 226), text="Dose 10 mg/kg")]}
    assert notation_edits(_analysis([], [table]), other) == ([], 1)


def test_a_formula_gets_its_reading_as_alt_text():
    """As on the standard route: a Formula's spoken form is its alt text, which PDF/UA asks of it."""
    text, said = "y = 2x + 1", "y equals 2 x plus 1"
    tagmap = {"elements": [_el(6, "Formula", text=text)]}
    edits, _ = notation_edits(_analysis([_read(text=text, said=said, role="Formula")]), tagmap)
    assert [(e["op"], e["element"], e["alt"]) for e in edits] == [("set_alt", 6, said)]
    # Adobe's own alt text stays
    assert notation_edits(_analysis([_read(text=text, said=said, role="Formula")]),
                          {"elements": [_el(6, "Formula", text=text, alt="a line")]}) == ([], 1)


def test_a_glyph_with_no_readable_meaning_matches_however_it_was_decoded():
    # The analysis and the tag map decode an unknown symbol differently: the rest must agree
    tagmap = {"elements": [_el(3, "P", text="The value � is given by H2O")]}
    block = _read(text="The value \U000f0021 is given by H2O", said="The value sigma is given by H 2 O")
    assert [e["element"] for e in notation_edits(_analysis([block]), tagmap)[0]] == [3]
    other = {"elements": [_el(3, "P", text="The value � is given by NH3")]}
    assert notation_edits(_analysis([block]), other) == ([], 1)


def test_math_that_comes_out_in_another_order_is_found_where_the_boxes_agree():
    # The tag map gives the pieces of a formula in another order and the bar over x as a character before it
    block = _read(bbox=(72, 88, 300, 104), text="x̄ = ∑ x i / n", said="x bar equals the sum of x i over n",
                  role="Formula")
    near = {"elements": [_el(6, "Formula", (74, 90, 296, 102), text="¯x = x i / n ∑")]}
    assert [e["element"] for e in notation_edits(_analysis([block]), near)[0]] == [6]
    # Letters alone agreeing isn't enough when the boxes barely overlap
    off = {"elements": [_el(6, "Formula", (250, 90, 500, 102), text="¯x = x i / n ∑")]}
    assert notation_edits(_analysis([block]), off) == ([], 1)


def test_a_tag_in_the_same_place_takes_the_reading_whatever_its_text_says():
    # A math font: the tag map decodes its glyphs as other letters than the analysis does
    block = _read(bbox=(72, 90, 300, 102), text="y = a x + b", said="y equals a x plus b", role="Formula")
    same_place = {"elements": [_el(6, "Formula", (73, 90, 299, 102), text="ŷ ≡ α χ ∔ β")]}
    assert [e["element"] for e in notation_edits(_analysis([block]), same_place)[0]] == [6]
    nearby = {"elements": [_el(6, "Formula", (72, 90, 400, 102), text="ŷ ≡ α χ ∔ β")]}
    assert notation_edits(_analysis([block]), nearby) == ([], 1)
