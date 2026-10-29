"""A long report's sections and page furniture, as a state audit report sets them (badpdfs.com liberty-county-boe.pdf,
production job d2f1285e): pages that open a section with a few words alone ("SECTION I" over "FINANCIAL"), bold
capitals a size smaller than the text heading its notes, a three-line running head reaching 13% down the page, and
page numbers in roman numerals or between dashes, a little above the bottom band. Before, it had 11 headings in 72
pages, 5 of them OCR noise, and every running head and page number was read as text."""
from app.pipeline.structure import assign_roles
from app.pipeline.structure.headings import is_heading_candidate
from tests.engine.layout import block, document, page

W, H = 595, 842


def _text_page(n, *extra, number_text=None):
    """A page of body text under the running head, with a page number at the foot."""
    blocks = [block(f"h{n}a", (196, 72, 402, 98), "LIBERTY COUNTY BOARD OF EDUCATION MANAGEMENT’S DISCUSSION",
                    page=n, size=10.6),
              block(f"h{n}b", (212, 96, 386, 110), "FOR THE YEAR ENDED JUNE 30, 2019", page=n, size=10.6),
              *extra,
              block(f"t{n}", (71, 300, 527, 360), "The School District uses many funds to account for a multitude "
                    "of financial transactions during the fiscal year.", page=n, size=10.6)]
    if number_text:
        blocks.append(block(f"n{n}", (519, 770, 527, 784), number_text, page=n, size=10.6))
    return page(*blocks, number=n, width=W, height=H)


def _divider(n, *lines):
    return page(*(block(f"d{n}{i}", (240, 380 + 30 * i, 360, 393 + 30 * i), text, page=n, size=10.6)
                  for i, text in enumerate(lines)), number=n, width=W, height=H)


def _report(*pages):
    pages = [page(block("cover", (180, 260, 420, 290), "ANNUAL FINANCIAL REPORT", size=20), width=W, height=H,
                  number=0), *pages]
    for n, p in enumerate(pages):
        p.number = n
        for b in p.blocks:
            b.page = n
    return document(*pages, body=10.6)


def test_a_page_with_a_few_words_alone_opens_a_section_at_the_top_level():
    doc = _report(_divider(1, "SECTION I", "FINANCIAL"),
                  *[_text_page(n) for n in range(2, 6)],
                  _divider(6, "SECTION II", "COMPLIANCE AND INTERNAL CONTROL REPORTS"),
                  *[_text_page(n) for n in range(7, 11)],
                  _divider(11, "(This page left intentionally blank)"))
    assign_roles(doc)
    heads = [(b.role, b.text) for p in doc.pages for b in p.blocks if b.role.startswith("H")]
    assert ("H1", "SECTION I FINANCIAL") in heads
    assert ("H1", "SECTION II COMPLIANCE AND INTERNAL CONTROL REPORTS") in heads
    assert [b.role for b in doc.pages[11].blocks] == ["P"]  # a blank page's note isn't a title


def test_smaller_bold_capitals_in_a_chart_are_labels_not_headings():
    """Smaller bold capitals head the text below them, alone on their row: in a chart, beside other labels, or with no
    text after them, they are labels (a pain chart's "OPIOID NAÏVE:" beside "Mild Pain")."""
    chart = [block("c1", (71, 120, 200, 132), "OPIOID NAÏVE:", size=9.7, bold=True, page=1),
             block("c2", (260, 120, 400, 132), "Mild Pain (Scale 1-3)", size=10.6, page=1),
             block("c3", (71, 140, 527, 200), "Start with the lowest dose and review the patient after three days "
                   "of treatment.", size=10.6, page=1)]
    doc = document(page(block("t", (71, 60, 400, 80), "Pain", size=16), number=0), page(*chart, number=1), body=10.6)
    assign_roles(doc)
    assert chart[0].role == "P"


def test_bold_capitals_a_size_smaller_than_the_text_are_headings():
    body = 10.6
    assert is_heading_candidate(block("a", (71, 120, 400, 132), "NOTE 1: DESCRIPTION OF SCHOOL DISTRICT", size=9.7,
                                      bold=True), body)
    assert is_heading_candidate(block("b", (71, 140, 200, 152), "REPORTING ENTITY", size=9.7, bold=True), body)
    # not in capitals, or not bold, or much smaller: no
    assert not is_heading_candidate(block("c", (71, 160, 200, 172), "Reporting entity", size=9.7, bold=True), body)
    assert not is_heading_candidate(block("d", (71, 180, 200, 192), "REPORTING ENTITY", size=9.7), body)
    assert not is_heading_candidate(block("e", (71, 200, 200, 210), "REPORTING ENTITY", size=8.0, bold=True), body)


def test_labelled_headings_rank_above_unlabelled_ones_of_the_same_style():
    notes = [block("n1", (71, 120, 400, 132), "NOTE 1: DESCRIPTION OF SCHOOL DISTRICT", size=9.7, bold=True, page=1),
             block("s1", (71, 140, 200, 152), "REPORTING ENTITY", size=9.7, bold=True, page=1),
             block("p1", (71, 160, 527, 220), "The Board of Education was established under the laws of the "
                   "State.", size=10.6, page=1),
             block("n2", (71, 240, 400, 252), "NOTE 2: SUMMARY OF ACCOUNTING POLICIES", size=9.7, bold=True, page=1),
             block("s2", (71, 260, 200, 272), "BASIS OF PRESENTATION", size=9.7, bold=True, page=1),
             block("p2", (71, 280, 527, 340), "The basic financial statements are comprised of the government-wide "
                   "statements.", size=10.6, page=1)]
    doc = document(page(block("t", (71, 60, 400, 80), "Notes", size=16), number=0), page(*notes, number=1), body=10.6)
    assign_roles(doc)
    roles = {b.id: b.role for b in notes}
    assert roles["n1"] == roles["n2"] and roles["s1"] == roles["s2"]
    assert int(roles["s1"][1]) == int(roles["n1"][1]) + 1


def test_a_running_head_a_little_below_the_top_band_is_page_furniture():
    doc = _report(*[_text_page(n, number_text=["i", "ii", "iii", "iv", "v"][n - 1]) for n in range(1, 6)])
    assign_roles(doc)
    for p in doc.pages[1:]:
        roles = {b.id[0]: b.role for b in p.blocks}
        assert roles["h"] == "Artifact" and roles["n"] == "Artifact" and roles["t"] == "P"


def test_a_page_number_between_dashes_is_page_furniture():
    doc = _report(*[_text_page(n, number_text=f"- {n} -") for n in range(1, 4)],
                  *[_text_page(n, number_text=f"-{n}-") for n in range(4, 6)])
    assign_roles(doc)
    assert all(b.role == "Artifact" for p in doc.pages for b in p.blocks if b.id.startswith("n"))


def test_a_line_below_the_top_band_with_text_above_it_isnt_a_running_head():
    """Only the topmost lines may be a running head below the band: a repeated line beside the page's own text, which
    runs on below the head area, is content (a table's repeated column heads on continued pages)."""
    pages = []
    for n in range(1, 5):
        pages.append(page(block(f"a{n}", (71, 40, 300, 160), f"Text on page {n} that differs, in a paragraph that "
                                "runs on down the page past the running head's area", page=n),
                          block(f"r{n}", (320, 100, 527, 112), "Fiscal Year 2019 Year 2018 Change", page=n),
                          block(f"t{n}", (71, 300, 527, 360), "The School District uses many funds to account for "
                                "transactions during the fiscal year.", page=n),
                          number=n, width=W, height=H))
    doc = _report(*pages)
    assign_roles(doc)
    assert all(b.role != "Artifact" for p in doc.pages for b in p.blocks if b.id.startswith("r"))
