"""Text layout rules: math typeset piece by piece, inline list items, list continuations, decoration."""
import io

import pikepdf
import pymupdf as fitz

from app.pipeline.analyzer import analyze_page
from app.pipeline.models import DocumentAnalysis
from app.pipeline.orchestrator import prepare_analysis
from app.pipeline.structure import assign_roles
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _blocks(path, page=0):
    pa, _ = analyze_page(fitz.open(path), page)
    return sorted((b for b in pa.blocks if b.role != "Artifact"), key=lambda b: (b.bbox[1], b.bbox[0]))


def _doc_with(draw, path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    draw(page)
    doc.save(path)
    return path


def test_superscript_joins_its_line_and_is_spoken(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "10", fontsize=11, fontname="tiro")
        p.insert_text((111.5, 195), "4", fontsize=7, fontname="tiro")  # raised, smaller
        p.insert_text((125, 200), "= 10 000", fontsize=11, fontname="tiro")
        p.insert_text((100, 300), "Plain body text so the page has a paragraph.", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "sup.pdf"))
    math = next(b for b in blocks if b.text.startswith("10"))
    assert math.text.replace(" ", "") == "104=10000", math.text
    assert math.has_scripts and math.actual_text == "10 to the power of 4 = 10 000"


def test_decimal_point_drawn_into_a_gap_reads_as_decimal(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "3 46", fontsize=11, fontname="tiro")  # gap left for the point
        w3 = fitz.get_text_length("3", fontname="tiro", fontsize=11)
        p.insert_text((100 + w3 + 0.4, 200), ".", fontsize=11, fontname="tiro")
        p.insert_text((100, 260), "Then a sentence, with commas, after it.", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "dec.pdf"))
    assert blocks[0].text == "3.46", blocks[0].text
    assert blocks[1].text == "Then a sentence, with commas, after it."


def test_several_list_items_on_one_line_are_separate_items(tmp_path):
    def draw(p):
        for x, t in ((97, "(a)  346 000 000"), (210, "(b)  2710"), (295, "(c)  0.000543")):
            p.insert_text((x, 200), t, fontsize=11, fontname="tiro")
        p.insert_text((97, 300), "A paragraph of ordinary text below the items.", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "inline.pdf"))
    assert [b.text for b in blocks[:3]] == ["(a) 346 000 000", "(b) 2710", "(c) 0.000543"]


def test_continuation_lines_stay_in_their_list_item(tmp_path):
    def draw(p):
        y = 200
        for label, first, cont in (("(a)", "2710 = 2.71 x 1000", "= 2.71 x 10"), ("(b)", "346 = 3.46 x 100", "= 3.46 x 10")):
            p.insert_text((97, y), label, fontsize=11, fontname="tiro")
            p.insert_text((127, y), first, fontsize=11, fontname="tiro")
            p.insert_text((160, y + 15), cont, fontsize=11, fontname="tiro")
            y += 40
    path = _doc_with(draw, tmp_path / "cont.pdf")
    analysis = prepare_analysis(path)
    items = [b for b in analysis.pages[0].blocks if b.role == "LI"]
    assert len(items) == 2 and all("= " in b.text.split(" ", 2)[-1] for b in items)
    assert not [b for b in analysis.pages[0].blocks if b.role == "P"]


def test_white_text_on_blank_page_is_not_content(tmp_path):
    def draw(p):
        p.insert_text((100, 150), "m e p", fontsize=12, color=(1, 1, 1))
        p.draw_rect(fitz.Rect(90, 290, 400, 320), color=None, fill=(0.1, 0.1, 0.4))
        p.insert_text((100, 310), "White on a dark banner", fontsize=12, color=(1, 1, 1))
    pa, _ = analyze_page(fitz.open(_doc_with(draw, tmp_path / "white.pdf")), 0)
    texts = [b.text for b in pa.blocks if b.role != "Artifact"]
    assert texts == ["White on a dark banner"]
    assert any(b.role == "Artifact" for b in pa.blocks)


def _icon_png() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 24, 24), False)
    pix.set_rect(pix.irect, (20, 20, 20))
    return pix.tobytes("png")


def test_repeated_small_icons_and_heading_icons_are_decorative(tmp_path):
    doc = fitz.open()
    png = _icon_png()
    other = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30), False)
    other.set_rect(other.irect, (200, 10, 10))
    for n in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((97, 120), "Worked Example %d" % (n + 1), fontsize=16, fontname="helv")
        page.insert_image(fitz.Rect(50, 100, 78, 128), stream=png)  # same icon on every page
        page.insert_text((97, 160), "Body text explaining the worked example in a full sentence.", fontsize=11)
    page = doc[0]
    page.insert_text((97, 400), "Note", fontsize=16, fontname="helv")
    page.insert_image(fitz.Rect(50, 382, 78, 410), stream=other.tobytes("png"))  # one-off icon beside a heading
    page.insert_image(fitz.Rect(150, 500, 450, 700), stream=other.tobytes("png"))  # a real picture
    path = tmp_path / "icons.pdf"
    doc.save(path)
    analysis = prepare_analysis(path)
    imgs = [(i.bbox, i.decorative) for p in analysis.pages for i in p.images]
    assert all(dec for bbox, dec in imgs if bbox[2] - bbox[0] < 40), imgs
    assert not [dec for bbox, dec in imgs if bbox[2] - bbox[0] > 200 and dec], "the big picture must stay content"


def test_split_tj_keeps_pixels_and_tags_each_piece(tmp_path):
    """A TJ that draws "(b) 2710" then jumps far right and draws "(c)" is split so each part is tagged
    with its own item, and the page still renders identically."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    content = (b"BT /F1 11 Tf 97 640 Td (\\(a\\) 346000000) Tj ET "
               b"BT /F1 11 Tf 210 640 Td [(\\(b\\) 2710) -5000 (\\(c\\) 0.000543)] TJ ET "
               b"BT /F1 11 Tf 97 560 Td (A sentence of body text below the list.) Tj ET")
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 595, 842],
                              Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                              Contents=pdf.make_stream(content))
    pdf.pages.append(pikepdf.Page(page))
    src = tmp_path / "tj.pdf"
    pdf.save(src)
    out = tmp_path / "tj_tagged.pdf"
    analysis = prepare_analysis(src)
    tag_document(src, out, analysis, title="T", language="en-US")
    with fitz.open(src) as a, fitz.open(out) as b:
        assert a[0].get_pixmap(dpi=72).samples == b[0].get_pixmap(dpi=72).samples
    with pikepdf.open(out) as tagged:
        findings, _ = check_integrity(tagged)
        assert not [f for f in findings if f.severity == "error"]
    items = [e for e in build_tagmap(out)["elements"] if e["role"] == "LBody"]
    assert [e["text"] for e in items] == ["(a) 346000000", "(b) 2710", "(c) 0.000543"], [e["text"] for e in items]


def test_stacked_fraction_reads_as_one_expression(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "D =", fontsize=11, fontname="tiro")
        p.insert_text((128, 192), "2a + b", fontsize=11, fontname="tiro")  # numerator over the bar
        p.draw_line((126, 196), (162, 196), width=0.5)
        p.insert_text((136, 207), "365", fontsize=11, fontname="tiro")  # denominator under it
        p.insert_text((100, 300), "A paragraph of ordinary text below the formula.", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "frac.pdf"))
    math = [b for b in blocks if "365" in b.text]
    assert len(math) == 1 and "D =" in math[0].text, [b.text for b in blocks]
    assert math[0].actual_text == "D = fraction, 2a + b, over 365, end fraction", math[0].actual_text


def test_simple_fraction_and_rows_without_a_bar(tmp_path):
    def draw(p):
        p.insert_text((100, 192), "1", fontsize=11, fontname="tiro")
        p.draw_line((98, 196), (106, 196), width=0.5)
        p.insert_text((100, 207), "2", fontsize=11, fontname="tiro")
        p.insert_text((110, 200), "of the cake", fontsize=11, fontname="tiro")
        # Two rows of a table with a rule between them aren't a fraction: the rows carry on past the rule
        p.insert_text((100, 400), "slope  m = rise / run", fontsize=11, fontname="tiro")
        p.draw_line((150, 404), (175, 404), width=0.5)
        p.insert_text((100, 415), "area of a circle A = pi r", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "frac2.pdf"))
    cake = next(b for b in blocks if "cake" in b.text)
    assert cake.actual_text == "1 over 2 of the cake", cake.actual_text
    assert not [b for b in blocks if b.actual_text and "over" in b.actual_text and "slope" in b.text]


def test_fake_bold_drawn_twice_in_separate_blocks_reads_once(tmp_path):
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    body = b"".join(b"BT /F1 10 Tf 97 %d Td (Line %d of ordinary body text in the page.) Tj ET " % (700 - 14 * i, i)
                    for i in range(30))
    content = (b"BT /F1 11 Tf 97 760 Td (Response: Resolved) Tj ET " + body +
               b"BT /F1 11 Tf 97.3 760 Td (Response:) Tj ET")
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 595, 842],
                              Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                              Contents=pdf.make_stream(content))
    pdf.pages.append(pikepdf.Page(page))
    src = tmp_path / "bold.pdf"
    pdf.save(src)
    texts = [b.text for b in _blocks(src)]
    assert any(t.startswith("Response: Resolved") for t in texts), texts[:3]
    assert not [t for t in texts if "RR" in t or "ee" in t], texts[:3]


def test_flattened_page_reads_like_the_original(tmp_path):
    """Flattening redraws text invisibly in another font: fractions, word order and hidden white text
    must read the same as on the original page."""
    from app.tagging.destructive import flatten_pages

    def draw(p):
        p.insert_text((100, 150), "m e p", fontsize=12, color=(1, 1, 1))  # invisible on the page
        p.insert_text((100, 200), "D =", fontsize=11, fontname="tiro")
        p.insert_text((128, 192), "2a + b", fontsize=11, fontname="tiro")
        p.draw_line((126, 196), (162, 196), width=0.5)
        p.insert_text((136, 207), "365", fontsize=11, fontname="tiro")
        # narrow serif text followed closely by a number: a wider font would run into it
        p.insert_text((100, 260), "The density of air is", fontsize=11, fontname="tiro")
        p.insert_text((100 + fitz.get_text_length("The density of air is ", "tiro", 11), 260), "1.3 kg", fontsize=11,
                      fontname="tiro")
    src = _doc_with(draw, tmp_path / "f.pdf")
    flat = tmp_path / "flat.pdf"
    flatten_pages(str(src), str(flat), [0])
    want = [(b.text, b.actual_text) for b in _blocks(src)]
    got = [(b.text, b.actual_text) for b in _blocks(flat)]
    assert got == want, (got, want)


def _tree(path, role_filter=("L", "LBody", "Span")):
    els = build_tagmap(path)["elements"]
    out = []

    def walk(i, d):
        e = els[i]
        if e["role"] in role_filter:
            own = next((els[k] for k in e["kids"] if els[k]["role"] == "Span"), None)
            text = (own.get("actual_text") if own else None) or e.get("actual_text") or e.get("text") or ""
            out.append((d, e["role"], text if e["role"] == "LBody" else ""))
        for k in e["kids"]:
            walk(k, d + (1 if e["role"] in role_filter else 0))
    for e in els:
        if e["parent"] is None:
            walk(e["i"], 0)
    return out


def test_lists_nest_by_label_kind_and_read_in_label_order(tmp_path):
    """Numbered questions with lettered parts and roman sub-parts become nested lists; parts laid out in
    a grid that runs down the columns are read in label order; a part labeled (i) after (h) is a letter."""
    def draw(p):
        t = lambda x, y, s: p.insert_text((x, y), s, fontsize=11, fontname="tiro")  # noqa: E731
        t(97, 100, "1. Write these numbers in standard form.")
        # a grid running down the columns: (a)(b)(c) in the first column, (d)(e)(f) in the second
        for col, labels in enumerate((("(a)", "(b)", "(c)"), ("(d)", "(e)", "(f)"))):
            for row, lab in enumerate(labels):
                t(127 + 150 * col, 120 + 18 * row, f"{lab} {row + 1}{col} 000")
        t(97, 200, "2. How far would the noise travel in:")
        t(127, 218, "(a) the first case")
        for k, lab in enumerate(("(i)", "(ii)", "(iii)")):
            t(156 + 90 * k, 236, f"{lab} {k + 1}0 s")
        t(127, 254, "(b) the second case")
        for k, lab in enumerate("cdefgh"):
            t(127, 272 + 18 * k, f"({lab}) part {lab}")
        t(127, 272 + 18 * 6, "(i) part i")
        t(97, 420, "3. The last question.")
    src = _doc_with(draw, tmp_path / "lists.pdf")
    out = tmp_path / "lists_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    tree = [(d, text.split(" ")[0]) for d, role, text in _tree(out) if role == "LBody"]
    labels = [lab for _, lab in tree]
    depths = [d for d, _ in tree]
    assert labels[:8] == ["1.", "(a)", "(b)", "(c)", "(d)", "(e)", "(f)", "2."], labels
    q1, q2, q3 = labels.index("1."), labels.index("2."), labels.index("3.")
    assert depths[q1] == depths[q2] == depths[q3]
    assert labels[q2 + 1:q2 + 7] == ["(a)", "(i)", "(ii)", "(iii)", "(b)", "(c)"], labels
    assert depths[q2 + 1] > depths[q2] and depths[q2 + 2] > depths[q2 + 1]  # 2. > (a) > (i)
    assert depths[q2 + 5] == depths[q2 + 1]  # (b) is back beside (a)
    # "(i)" after "(h)" is the next letter, a sibling of (h)
    last_i = len(labels) - 1 - labels[::-1].index("(i)")
    assert labels[last_i - 1] == "(h)" and tree[last_i][0] == tree[last_i - 1][0]


def test_item_with_nested_list_keeps_read_aloud_text_off_the_nested_items(tmp_path):
    """ActualText on an LBody would replace everything inside it, nested list included."""
    def draw(p):
        p.insert_text((97, 100), "1. The speed is 3 x 10", fontsize=11, fontname="tiro")
        p.insert_text((97 + fitz.get_text_length("1. The speed is 3 x 10", "tiro", 11) + 0.5, 95), "2", fontsize=7,
                      fontname="tiro")
        p.insert_text((127, 120), "(a) first part", fontsize=11, fontname="tiro")
        p.insert_text((127, 138), "(b) second part", fontsize=11, fontname="tiro")
    src = _doc_with(draw, tmp_path / "nested_at.pdf")
    out = tmp_path / "nested_at_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    els = build_tagmap(out)["elements"]
    top = next(e for e in els if e["role"] == "LBody" and "speed" in (e.get("text") or ""))
    assert not top.get("actual_text"), "ActualText on the LBody would hide its nested list"
    span = next(els[k] for k in top["kids"] if els[k]["role"] == "Span")
    assert "squared" in span["actual_text"]


def test_headings_that_look_alike_get_the_same_level(tmp_path):
    """A one-off cover-page style doesn't push later headings down; same-looking headings share a level;
    numbered sections at one depth share a level; a slightly larger copy of a repeated heading pattern
    joins its pattern."""
    doc = fitz.open()
    body = "Body text explaining the topic in a full sentence that runs across the page."
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Unit Cover Title", fontsize=25, fontname="hebo")
    p.insert_text((72, 140), "TEXT", fontsize=17, fontname="hebo")
    for n in (1, 2):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 80), f"{n}.1 Section heading", fontsize=22, fontname="helv")
        y = 120
        for k in (1, 2):
            size = 16 if (n, k) == (2, 2) else 14  # one copy of the pattern drawn larger
            p.insert_text((72, y), f"Worked Example {k}", fontsize=size, fontname="helv")
            p.insert_text((72, y + 24), "Solution", fontsize=14, fontname="hebo")
            for j in range(3):
                p.insert_text((72, y + 48 + 14 * j), body, fontsize=11, fontname="helv")
            y += 120
    path = tmp_path / "heads.pdf"
    doc.save(path)
    analysis = prepare_analysis(path)
    levels = [(b.text, b.role) for pg in analysis.pages for b in pg.blocks if b.role.startswith("H")]
    by_text: dict = {}
    for text, role in levels:
        by_text.setdefault(text.split(" ")[0] if text[0].isdigit() else text.rstrip("12 "), set()).add(role)
    assert by_text["1.1"] == {"H2"} and by_text["2.1"] == {"H2"}, levels
    assert by_text["Worked Example"] == {"H3"}, levels
    assert by_text["Solution"] == {"H4"}, levels
    nums = [int(r[1]) for _, r in levels]
    assert all(b <= a + 1 for a, b in zip(nums, nums[1:])), levels  # no skipped levels


def _spoken(path):
    """Read-aloud text of each block after role assignment (Formula alt text, or ActualText)."""
    doc = prepare_analysis(path)
    return [(b.role, b.actual_text or b.text) for p in doc.pages for b in p.blocks if b.role != "Artifact"]


def test_stacked_and_side_by_side_equations_are_read_separately(tmp_path):
    def power(p, x, y, base, exp, rest):
        p.insert_text((x, y), base, fontsize=11, fontname="tiro")
        p.insert_text((x + 11.5, y - 5), exp, fontsize=7, fontname="tiro")
        p.insert_text((x + 22, y), rest, fontsize=11, fontname="tiro")

    def draw(p):
        p.insert_text((100, 100), "Remember that powers of ten work like this in every example below.",
                      fontsize=11, fontname="tiro")
        # three stacked equations, and a second column of equations far to the right
        for i, (e, v, ne, nv) in enumerate([("4", "= 10 000", "-4", "= 0.0001"), ("3", "= 1000", "-3", "= 0.001"),
                                            ("2", "= 100", "-2", "= 0.01")]):
            power(p, 100, 140 + 16 * i, "10", e, v)
            power(p, 300, 140 + 16 * i, "10", ne, nv)
    spoken = _spoken(_doc_with(draw, tmp_path / "eqs.pdf"))
    formula = next(t for r, t in spoken if r == "Formula")
    assert formula == ("10 to the power of 4 = 10 000; 10 to the power of -4 = 0.0001; 10 cubed = 1000; "
                       "10 to the power of -3 = 0.001; 10 squared = 100; 10 to the power of -2 = 0.01"), formula
    prose = next(t for r, t in spoken if r == "P")
    assert ";" not in prose


def test_continued_equations_and_prose_are_not_split():
    from app.pipeline.models import join_statements

    # a derivation carried onto the next line, and an equation continued after "and"
    assert join_statements([("4 × 3 = 12", "4 × 3 = 12"), ("= 1.2 × 10", "= 1.2 × 10")]) == "4 × 3 = 12 = 1.2 × 10"
    assert join_statements([("x = 1 and", "x = 1 and"), ("y = 2", "y = 2")]) == "x = 1 and y = 2"
    assert join_statements([("a + b =", "a + b ="), ("c = d", "c = d")]) == "a + b = c = d"
    # prose that happens to contain equations on consecutive lines stays one sentence
    assert join_statements([("Since the total is x = 4 we know that", "Since the total is x = 4 we know that"),
                            ("the remainder is y = 2 for this case", "the remainder is y = 2 for this case")]) \
        == "Since the total is x = 4 we know that the remainder is y = 2 for this case"
    # separate statements pause
    assert join_statements([("a = 1", "a = 1"), ("b = 2", "b = 2")]) == "a = 1; b = 2"


def test_raised_prime_reads_as_prime(tmp_path):
    def draw(p):
        p.insert_text((100, 200), "F", fontsize=11, fontname="tiro")
        p.insert_text((106.5, 195), "'", fontsize=7, fontname="tiro")  # raised prime mark
        p.insert_text((115, 200), "= 2x", fontsize=11, fontname="tiro")
        p.insert_text((100, 300), "Plain body text so the page has a paragraph.", fontsize=11, fontname="tiro")
    blocks = _blocks(_doc_with(draw, tmp_path / "prime.pdf"))
    math = next(b for b in blocks if b.text.startswith("F"))
    assert math.actual_text == "F prime = 2x", math.actual_text


def test_prime_marks_in_spoken_text_are_words():
    from app.pipeline.analyzer import _speak_primes

    assert _speak_primes("b sub F ′ and f ″") == "b sub F prime and f double prime"
    assert _speak_primes("don't change apostrophes") == "don't change apostrophes"
