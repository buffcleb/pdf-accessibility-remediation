"""Reading order where columns sit under a title narrower than the page, headings from rows of controls, and a
page whose picture is cut off at its edge (found on a web page "printed" to PDF as a picture: a recipe with
Ingredients beside Directions)."""
import pikepdf
import pymupdf as fitz

from app.pipeline.models import PageAnalysis, TextBlock
from app.pipeline.orchestrator import _covers_page
from app.pipeline.structure import detect_columns, heuristic_reading_order
from app.pipeline.structure.headings import is_heading_candidate
from app.verification.integrity import check_integrity
from app.verification.semantic import SemanticChecker


def _page(blocks):
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792))
    page.blocks = [TextBlock(id=i, page=0, bbox=bbox, text=text, font_size=size, role=role)
                   for i, bbox, text, size, role in blocks]
    return page


def _order(page):
    return [page.element(e).text for e in heuristic_reading_order(page)]


def _recipe():
    """A logo, then a title and a line of buttons (neither as wide as the page), then two columns whose lines
    don't line up: short ingredients on the left, steps on the right."""
    blocks = [("logo", (138, 40, 194, 56), "Logo", 15, "P"),
              ("title", (353, 149, 555, 169), "Sourdough Pizza", 20, "H1"),
              ("by", (353, 179, 465, 188), "from example.org", 9, "P"),
              ("ctl", (352, 217, 584, 233), "1 serving", 11, "P"),
              ("hl", (227, 296, 322, 313), "Ingredients", 16, "H2"),
              ("hr", (494, 295, 557, 308), "Directions", 16, "H2")]
    left = [330, 384, 417, 450, 483, 537, 570, 624, 678, 711]
    right = [331, 351, 372, 408, 429, 471, 513, 549, 612, 669, 690, 732]
    blocks += [(f"l{n}", (227, y, 400, y + 12), f"ingredient {n}", 11, "P") for n, y in enumerate(left)]
    blocks += [(f"r{n}", (494, y, 580, y + 10), f"step {n}", 11, "P") for n, y in enumerate(right)]
    return _page(blocks)


def test_columns_under_a_title_narrower_than_the_page_are_read_one_after_the_other():
    page = _recipe()
    order = _order(page)
    assert order[:4] == ["Logo", "Sourdough Pizza", "from example.org", "1 serving"]
    assert order[4:15] == ["Ingredients"] + [f"ingredient {n}" for n in range(10)]
    assert order[15:] == ["Directions"] + [f"step {n}" for n in range(12)]
    assert detect_columns(page) == 2  # short lines count: the page is offered to the AI for its order too


def test_a_grid_that_lines_up_row_by_row_is_read_across():
    """Exercise items "(a) (b) (c)" under their question: across each row, as before, not down each column. A
    fraction or power moves a line's top by a few points; that's still the same row."""
    blocks = [("q", (99, 82, 400, 94), "5. Give the answers", 11, "P")]
    letters = iter("abcdefghijkl")
    for row, y in enumerate((105, 122, 160, 187)):
        for col, x in enumerate((127, 241, 354)):
            lift = 6 if (row, col) in ((1, 2), (2, 2)) else 0
            c = next(letters)
            blocks.append((c, (x, y + lift, x + 70, y + lift + 12), f"({c})", 11, "P"))
    order = _order(_page(blocks))
    assert order == ["5. Give the answers"] + [f"({c})" for c in "abcdefghijkl"]


def test_a_heading_below_both_columns_is_read_after_them():
    """An article's first page: a sidebar beside the abstract, then the first section's heading under the sidebar,
    starting lower than the abstract ends."""
    blocks = [("t", (101, 120, 480, 150), "Title of the article", 18, "H1"),
              ("s1", (101, 215, 230, 240), "Author", 9, "P"), ("s2", (101, 253, 230, 275), "Department", 9, "P"),
              ("s3", (101, 284, 230, 330), "Correspondence", 9, "P"), ("s4", (101, 341, 230, 380), "Funding", 9, "P"),
              ("a1", (265, 259, 520, 270), "Abstract", 10, "H2"), ("a2", (265, 271, 520, 510), "Abstract text", 10, "P"),
              ("a3", (265, 523, 520, 545), "Keywords", 9, "P"),
              ("h", (101, 578, 200, 590), "Introduction", 12, "H2")]
    assert _order(_page(blocks)) == ["Title of the article", "Author", "Department", "Correspondence", "Funding",
                                     "Abstract", "Abstract text", "Keywords", "Introduction"]


def test_a_row_of_controls_is_not_a_heading():
    big = dict(page=0, bbox=(0, 0, 200, 16), font_size=16)
    assert not is_heading_candidate(TextBlock(id="a", text="[- 1 serving + | © 1hour 22 m", **big), 11)
    assert is_heading_candidate(TextBlock(id="b", text="Ingredients", **big), 11)
    assert is_heading_candidate(TextBlock(id="c", text="C++ and C#: an introduction", **big), 11)
    assert is_heading_candidate(TextBlock(id="d", text="2.1 Cost (in $) – a summary", **big), 11)


def test_a_scan_is_the_page_however_it_is_placed():
    assert _covers_page((0, 0, 612, 792), 612, 792)
    assert _covers_page((28, 28, 1100, 736), 612, 792)      # inside margins, and wider than the page
    assert not _covers_page((223, 142, 337, 255), 612, 792)  # a photo on the page
    assert not _covers_page((700, 0, 1300, 792), 612, 792)   # entirely off the page


def _picture_page(tmp_path, rect):
    """A tagged page with one picture drawn at rect (an artifact) and a paragraph."""
    path = tmp_path / "p.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pix.set_rect(pix.irect, (200, 60, 40))
    page.insert_image(fitz.Rect(*rect), pixmap=pix)
    doc.save(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pg = pdf.pages[0]
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        p = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, P=root, Pg=pg.obj, K=0))
        root.K = pikepdf.Array([p])
        root.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([p])])))
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pg.obj.StructParents = 0
        pdf.save(path)
    return path


def _cut_off(path):
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        checker = SemanticChecker(path, pdf, index)
        checker._cut_off()
        return [f for f in checker.findings if f.issue == "part of the page is cut off"]


def test_a_page_wider_than_its_paper_is_flagged(tmp_path):
    found = _cut_off(_picture_page(tmp_path, (28, 28, 1100, 736)))
    assert len(found) == 1 and found[0].page == 1 and found[0].severity == "review"
    assert "45%" in found[0].detail or "44%" in found[0].detail or "46%" in found[0].detail


def test_a_page_that_fits_is_not_flagged(tmp_path):
    assert _cut_off(_picture_page(tmp_path, (28, 28, 584, 764))) == []
    # A small picture bleeding off the edge is a design choice
    assert _cut_off(_picture_page(tmp_path, (500, 600, 700, 760))) == []


def test_a_page_of_thousands_of_small_pieces_is_ordered_quickly():
    """Printed music is drawn as thousands of tiny pictures (4,000 on one page of a real textbook). Looking for
    columns must stay quick: the first version of this search took minutes on such a page."""
    import time

    from app.pipeline.models import ImageRegion

    page = _page([("t", (100, 60, 500, 80), "Chapter 4", 18, "H1"), ("p", (100, 700, 500, 730), "Some text below", 11, "P")])
    n = 0
    for row in range(40):
        for col in range(100):
            x, y = 100 + col * 4.0, 120 + row * 14.0 + (col % 3)
            page.images.append(ImageRegion(id=f"i{n}", page=0, bbox=(x, y, x + 3, y + 5), xref=0))
            n += 1
    started = time.monotonic()
    order = heuristic_reading_order(page)
    detect_columns(page)
    assert time.monotonic() - started < 5
    assert len(order) == n + 2 and order[0] == "t" and order[-1] == "p"


def test_matching_drawn_pictures_to_the_pages_pictures_is_quick_and_unchanged():
    """The tagger asks, for every picture a page draws, which of the page's pictures it is. With thousands of them
    it looks only at the ones nearby (and the first drawn from the same object), and gets the same answer as
    looking at them all."""
    import random
    import time

    from app.pipeline.models import ImageRegion
    from app.tagging.targets import PageResolver

    random.seed(7)
    page = _page([("t", (100, 60, 500, 80), "Chapter 4", 18, "H1")])
    for n in range(4000):
        x, y = random.uniform(40, 560), random.uniform(100, 740)
        w, h = random.choice(((3, 5), (6, 6), (40, 30), (0, 8)))
        page.images.append(ImageRegion(id=f"i{n}", page=0, bbox=(x, y, x + w, y + h), xref=n % 37,
                                       decorative=n % 5 != 0))
    near, full = PageResolver(page), PageResolver(page)
    full._images_near = lambda bbox, xref=0: full.images
    boxes = [(i.bbox, i.xref) for i in page.images] + [
        ((i.bbox[0] - 1, i.bbox[1] + 1, i.bbox[2] + 2, i.bbox[3] + 3), 0) for i in page.images[::7]]
    started = time.monotonic()
    got = [(near._image(b, x), near._form(b)) for b, x in boxes]
    assert time.monotonic() - started < 5
    sample = random.sample(range(len(boxes)), 300)
    assert [got[k] for k in sample] == [(full._image(*boxes[k]), full._form(boxes[k][0])) for k in sample]
