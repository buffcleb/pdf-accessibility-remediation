"""A Word thesis, read badly (#665): its three-line tables weren't found, its chapter numbers stood apart from their
titles, a displayed equation in pieces became a heading and three pictures, its contents weren't a TOC, captions and
headings that wrapped under a hanging indent were cut in two, its title was the cover's "PhD Thesis", and running
heads that change with the chapter were read as text."""
import pikepdf
import pymupdf as fitz
from pikepdf import Name

from app.pipeline.analyzer import _extract_blocks, _extract_tables, _merge_paragraph_lines, _equation_lines
from app.pipeline.models import ImageRegion
from app.pipeline.orchestrator import JobOptions, _pick_title, prepare_analysis
from app.pipeline.structure import assign_roles
from app.pipeline.structure.artifacts import mark_running_artifacts
from app.pipeline.structure.headings import assign_headings, join_chapter_numbers
from app.pipeline.structure.lists_captions import assign_lists_and_captions, attach_list_continuations
from app.tagging.tagger import tag_document
from tests.engine.layout import block, document, page


# ----------------------------------------------------------------------------------------------- three-line tables
def _ruled_page(path, rules, rows, cols=(100, 250, 400, 520)):
    """A page with horizontal rules only, each drawn as one thin rectangle per column (as Word draws cell borders),
    and rows of (y, [cell text, ...])."""
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    for y in rules:
        for x0, x1 in zip(cols, cols[1:]):
            p.draw_rect(fitz.Rect(x0 + 0.5, y, x1 - 0.5, y + 0.5), color=None, fill=(0, 0, 0))
    for y, cells in rows:
        for x0, text in zip(cols, cells):
            if text:
                p.insert_text((x0 + 4, y), text, fontsize=10)
    doc.save(path)
    return fitz.open(path)


def test_a_table_drawn_with_three_rules_is_a_table(tmp_path):
    doc = _ruled_page(tmp_path / "t.pdf", (100, 120, 200),
                      [(114, ["Process", "Catalyst", "Product"]), (135, ["Route one", "Cu", "Ethanal"]),
                       (150, ["Route two", "Ni, MgO,", "Methane"]), (165, ["", "SiO2", ""]),
                       (180, ["Route three", "Ag", "Oxirane"])])
    tables = _extract_tables(doc[0], 0)
    assert len(tables) == 1
    t = tables[0]
    assert (t.n_rows, t.n_cols) == (4, 3)
    grid = {(c.row, c.col): c.text for c in t.cells}
    assert [grid[0, c] for c in range(3)] == ["Process", "Catalyst", "Product"]
    assert grid[2, 1] == "Ni, MgO, SiO2"  # a cell's wrapped line goes on in its row
    assert all(c.is_header == (c.row == 0) for c in t.cells) and t.header_evidence


def test_a_caption_between_rules_ends_a_table(tmp_path):
    doc = _ruled_page(tmp_path / "t.pdf", (100, 120, 160, 300, 320, 360),
                      [(114, ["Entry", "Time", "Yield"]), (135, ["1", "10", "54"]), (150, ["2", "20", "61"]),
                       (240, ["Table 2 Yields after reloading the gases", "", ""]),
                       (314, ["Entry", "Charges", "Yield"]), (335, ["1", "3", "39"]), (350, ["2", "2", "24"])])
    tables = _extract_tables(doc[0], 0)
    assert [(t.n_rows, t.n_cols) for t in tables] == [(3, 3), (3, 3)]


def test_rules_with_prose_between_them_are_no_table(tmp_path):
    doc = _ruled_page(tmp_path / "t.pdf", (100, 300, 500),
                      [(200, ["A paragraph of ordinary prose that runs on across the columns of the page", "", ""])],
                      cols=(100, 520))
    assert _extract_tables(doc[0], 0) == []


# ----------------------------------------------------------------------------------------------- chapter numbers
def test_a_chapter_number_set_huge_joins_its_title():
    num = block("n", (470, 88, 510, 177), "3", size=80, bold=True)
    title = block("t", (121, 99, 332, 172), "Organic solvents", size=24, bold=True)
    d = document(page(num, title), body=11)
    join_chapter_numbers(d)
    assert [b.text for b in d.pages[0].blocks] == ["3 Organic solvents"]
    assert d.pages[0].blocks[0].bbox == (121, 88, 510, 177)


def test_chapters_in_different_sizes_are_one_level():
    pages = [page(block("a", (100, 100, 300, 130), "1 Introduction", size=28, bold=True, page=0),
                  block("b", (100, 200, 300, 212), "1.1 Catalysis", size=11, bold=True, page=0), number=0),
             page(block("c", (100, 100, 300, 130), "2 Organic solvents", size=22, bold=True, page=1),
                  block("d", (100, 200, 300, 212), "2.1 Introduction", size=11, bold=True, page=1), number=1),
             page(block("e", (100, 100, 300, 130), "3 Conclusions", size=28, bold=True, page=2), number=2)]
    d = document(*pages, body=11)
    assign_headings(d)
    assert [b.role for p in d.pages for b in p.blocks] == ["H1", "H2", "H1", "H2", "H1"]


def test_a_huge_number_on_the_titles_line_is_a_block_of_its_own(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((100, 150), "using a continuous gas flow reactor", fontsize=22)
    p.insert_text((480, 150), "4", fontsize=72)
    texts = sorted(b.text for b in _extract_blocks(p, 0))
    assert texts == ["4", "using a continuous gas flow reactor"]


def test_a_section_number_typed_with_a_space_is_a_heading_not_a_list():
    d = document(page(block("a", (100, 100, 300, 112), "2.5 Methods", size=11, bold=True),
                      block("b", (100, 200, 300, 212), "2. 5.4 Reliability of results", size=11, bold=True)), body=11)
    assign_headings(d)
    assert [b.role for b in d.pages[0].blocks] == ["H1", "H2"]


def test_levels_are_checked_top_to_bottom_whatever_the_blocks_order():
    # The chapter's title is listed last on its page; its sections on the next page still sit under it
    p0 = page(block("s1", (100, 300, 300, 312), "2.1 Materials", size=11, bold=True),
              block("s2", (100, 400, 300, 412), "2.1.1 Reagents", size=11, bold=True),
              block("t", (100, 100, 300, 130), "2 Experimental", size=28, bold=True), number=0)
    p1 = page(block("s3", (100, 100, 300, 112), "2.1.2 Gases", size=11, bold=True, page=1), number=1)
    d = document(p0, p1, body=11)
    assign_headings(d)
    assert d.pages[1].blocks[0].role == "H3"


# ----------------------------------------------------------------------------------------------- equation lines
def test_an_equation_in_pieces_beside_its_number_is_one_formula():
    p = page(block("top", (249, 547, 265, 560), "Ni", size=16), block("bot", (249, 575, 265, 593), "Nj", size=16),
             block("exp", (311, 553, 332, 562), "Ei - Ej", size=8), block("lbl", (380, 561, 436, 573), "Equation 1.1"),
             block("prose", (113, 515, 465, 527), "The share of particles in each state follows this"),
             ImageRegion(id="i1", page=0, bbox=(242, 547, 285, 586), xref=5),
             ImageRegion(id="i2", page=0, bbox=(307, 570, 337, 579), xref=6))
    _equation_lines(p)
    assert [b.id for b in p.blocks] == ["lbl", "prose"]
    [eq] = p.images
    assert (eq.kind, eq.holds_pieces, eq.drawing) == ("formula", True, "equation")
    assert eq.bbox == (242, 547, 337, 593)


def test_a_sentence_ending_in_a_bracketed_number_is_no_equation():
    p = page(block("s", (100, 300, 400, 312), "as was found before in two of the runs"),
             block("lbl", (420, 300, 450, 312), "(2.1)"),
             ImageRegion(id="i1", page=0, bbox=(300, 299, 320, 313), xref=5))
    _equation_lines(p)
    assert len(p.blocks) == 2 and p.images[0].id == "i1" and not p.images[0].holds_pieces


# ----------------------------------------------------------------------------------------------- indented lines
def test_a_captions_hanging_indent_goes_on_with_it():
    first = block("a", (113, 328, 511, 340), "Figure 1.1 Reaction energy profile for an uncatalysed reaction and")
    rest = block("b", (185, 347, 511, 373), "a catalysed reaction, showing a lower barrier.")
    assert [b.text[:10] for b in _merge_paragraph_lines([first, rest])] == ["Figure 1.1"]


def test_a_first_line_indent_goes_on_with_the_paragraph():
    first = block("a", (149, 382, 510, 395), "In this work a new route to the product was tried and")
    rest = block("b", (113, 401, 510, 546), "found to give a better yield than the old one.")
    assert len(_merge_paragraph_lines([first, rest])) == 1


def test_an_indented_new_paragraph_starts_a_block_of_its_own():
    end = block("a", (113, 382, 510, 395), "and so the reaction stops once the gases are used up.")
    new = block("b", (149, 401, 510, 413), "The results fall into three main groups below")
    assert len(_merge_paragraph_lines([end, new])) == 2


# ----------------------------------------------------------------------------------------------- contents
def _contents_pdf(path, title=True):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    y = 90
    if title:
        p.insert_text((113, y), "Table of Contents", fontsize=24)
        y += 50
    entries = [(113, "Chapter 1: Introduction", "1"), (113, "1.1 Catalyst and Catalysis", "1"),
               (113, "1.2 Classes of catalysis", "3"), (149, "1.2.1 Applications", "10"),
               (149, "1.2.2 Manufacturing", "11"), (149, "1.2.3 A Faster Route to the Product", "20"),
               (174, "in a Small Glass Vessel", None), (113, "1.3 Gold Catalysis", "22"),
               (113, "1.4 Summary", "38"), (113, "1.5 References", "40")]
    for x, text, num in entries:
        p.insert_text((x, y), text, fontsize=11)
        if num:
            p.insert_text((485 - fitz.get_text_length(num, fontsize=11), y), num, fontsize=11)
        y += 19
    doc.save(path)
    return path


def test_contents_entries_are_toci_one_per_entry(tmp_path):
    a = prepare_analysis(_contents_pdf(tmp_path / "c.pdf"))
    roles = [(b.role, b.text) for b in sorted(a.pages[0].blocks, key=lambda b: b.bbox[1])]
    assert roles[0] == ("H1", "Table of Contents")
    entries = [t for r, t in roles if r == "TOCI"]
    assert len(entries) == 9
    assert "1.2.3 A Faster Route to the Product 20 in a Small Glass Vessel" in entries
    assert not any(r.startswith("H") for r, t in roles[1:])


def test_a_contents_page_without_its_title_is_found_by_its_entries(tmp_path):
    a = prepare_analysis(_contents_pdf(tmp_path / "c.pdf", title=False))
    assert sum(b.role == "TOCI" for b in a.pages[0].blocks) == 9


def test_contents_are_tagged_toc_nested_by_indent(tmp_path):
    src, out = _contents_pdf(tmp_path / "c.pdf"), tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        tocs = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and o.get("/S") == Name.TOC]
        top = [t for t in tocs if t.P.get("/S") != Name.TOCI]
        assert len(top) == 1
        items = [k for k in top[0].K if k.get("/S") == Name.TOCI]
        assert len(items) == 6  # the chapter, 1.1, 1.2 (holding 1.2.1 to 1.2.3), 1.3, 1.4 and 1.5
        nested = [t for t in tocs if t.P.get("/S") == Name.TOCI]
        assert len(nested) == 1 and len([k for k in nested[0].K if k.get("/S") == Name.TOCI]) == 3


# ----------------------------------------------------------------------------------------------- the front matter
def test_a_cover_page_has_one_heading():
    cover = page(block("a", (200, 100, 400, 116), "PhD Thesis", size=16, bold=True),
                 block("b", (200, 140, 400, 156), "School of Chemistry", size=16),
                 block("c", (100, 200, 500, 216), "The direct synthesis of hydrogen peroxide", size=16, bold=True),
                 block("d", (200, 300, 400, 314), "Thesis submitted for the degree of PhD", size=14),
                 block("e", (200, 400, 400, 416), "A. N. Author", size=16, bold=True), number=0)
    body = page(block("f", (100, 100, 300, 130), "Abstract", size=18, bold=True, page=1),
                block("g", (100, 200, 500, 300), "In this thesis " * 20, size=11, page=1), number=1)
    rest = [page(block(f"x{n}", (100, 200, 500, 300), "Body text " * 20, page=n), number=n) for n in range(2, 12)]
    d = document(cover, body, *rest, body=11)
    assign_headings(d)
    assert [b.role for b in cover.blocks] == ["P", "P", "H1", "P", "P"]
    assert body.blocks[0].role == "H1"
    # ...and the title, the first H1, is the cover's title, not its "PhD Thesis"
    assert _pick_title(JobOptions(stem="x"), d) == "The direct synthesis of hydrogen peroxide"


def test_a_short_documents_first_page_keeps_its_headings():
    d = document(page(block("a", (100, 100, 300, 130), "Skillet Pizza", size=20, bold=True),
                      block("b", (100, 200, 300, 216), "Ingredients", size=16, bold=True),
                      block("c", (100, 300, 300, 316), "Directions", size=16, bold=True),
                      block("d", (100, 400, 300, 416), "Notes", size=16, bold=True),
                      block("e", (100, 500, 300, 516), "Serving", size=16, bold=True)), body=11)
    assign_headings(d)
    assert [b.role for b in d.pages[0].blocks] == ["H1", "H2", "H2", "H2", "H2"]


def test_a_dedication_in_large_plain_print_is_no_heading():
    d = document(page(block("a", (220, 296, 392, 316), "Dedicated to my family.", size=18),
                      block("b", (100, 100, 300, 130), "Acknowledgements", size=18, bold=True)), body=11)
    assign_headings(d)
    assert [b.role for b in d.pages[0].blocks] == ["P", "H1"]


# ----------------------------------------------------------------------------------------------- running heads
def _with_head(n, head, size=10.0):
    return page(block(f"h{n}", (280, 36, 510, 50), head, size=size, page=n),
                block(f"b{n}", (100, 200, 500, 300), "Body text of the page.", size=11, page=n), number=n)


def test_a_running_head_that_changes_with_the_chapter_is_an_artifact():
    pages = [_with_head(n, "Chapter 1 Introduction") for n in range(3)] + \
            [_with_head(n, "Chapter 2 Experimental") for n in range(3, 6)] + \
            [_with_head(n, f"Part {chr(65 + n)} on its own") for n in range(6, 14)]
    d = document(*pages, body=11)
    mark_running_artifacts(d)
    roles = [p.blocks[0].role for p in d.pages]
    assert roles[:6] == ["Artifact"] * 6
    assert roles[6:] == ["P"] * 8  # a line in that place on one page only is the page's own


def test_a_slide_title_on_three_slides_in_a_row_stays():
    d = document(*[_with_head(n, "Worked Example", size=24) for n in range(3)],
                 *[page(block(f"x{n}", (100, 200, 500, 300), "Body", page=n), number=n) for n in range(3, 10)],
                 body=11)
    mark_running_artifacts(d)
    assert all(p.blocks[0].role == "P" for p in d.pages[:3])


# ----------------------------------------------------------------------------------------------- lists
def test_paragraphs_numbered_without_full_stops_are_a_list():
    lines = ["1 Planning - A careful plan saves time later", "2 Records - Notes should list every",
             "3 Clean Benches - Wherever it can be done", "a sample kept on the bench should be labelled"]
    p = page(*[block(f"b{i}", (113, 100 + 40 * i, 524, 112 + 40 * i), t) for i, t in enumerate(lines)])
    d = document(p, body=11)
    assign_lists_and_captions(d)
    assert [b.role for b in p.blocks] == ["LI", "LI", "LI", "P"]
    assert [b.list_label for b in p.blocks[:3]] == ["1", "2", "3"]


def test_two_paragraphs_starting_with_numbers_are_not_a_list():
    p = page(block("a", (113, 100, 524, 112), "1 Sample was taken from each batch"),
             block("b", (113, 140, 524, 152), "2 Samples were taken from the last"))
    d = document(p, body=11)
    assign_lists_and_captions(d)
    assert [b.role for b in p.blocks] == ["P", "P"]


def test_a_flush_lower_case_line_goes_on_with_the_item_above():
    p = page(block("a", (113, 632, 524, 644), "12 Safe Storage - Bottles and the shelves they", role="LI",
                   list_label="12"),
             block("b", (113, 651, 524, 682), "stand on in a shared store room should be checked"))
    d = document(p, body=11)
    attach_list_continuations(d)
    assert len(p.blocks) == 1


def test_a_bracket_closed_on_the_next_page_is_no_list_item():
    p0 = page(block("a", (113, 700, 510, 760), "The catalyst was charged (10", page=0), number=0)
    p1 = page(block("b", (113, 76, 510, 200), "g) and solvent were added to the vessel.", page=1), number=1)
    d = document(p0, p1, body=11)
    assign_lists_and_captions(d)
    assert p1.blocks[0].role == "P"


def test_the_whole_thesis_layout_runs_in_order():
    # (the passes above run together without one undoing another)
    d = document(page(block("n", (470, 88, 510, 177), "1", size=80, bold=True),
                      block("t", (121, 99, 332, 172), "Introduction", size=28, bold=True),
                      block("s", (113, 300, 300, 312), "1.1 Catalysis", size=11, bold=True)), body=11)
    assign_roles(d)
    assert [(b.role, b.text) for b in d.pages[0].blocks] == [("H1", "1 Introduction"), ("H2", "1.1 Catalysis")]
