"""Chemical structures and reaction schemes drawn with lines are whole figures, with the atom labels on them.
Only some ring systems were figures (and only the rings); other molecules were left out, and each "O", "OH" and
"OAc" was a paragraph of its own. The drawings here are made up."""
import math

import pymupdf as fitz

from app.pipeline import schemes
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.tagmap import build_tagmap
from tests.engine.pdfs import integrity_errors

BOND = 14.0


def _ring(cx, cy, sides=6):
    pts = [(cx + BOND * math.cos(math.pi / 2 + 2 * math.pi * k / sides), cy + BOND * math.sin(math.pi / 2 + 2 * math.pi * k / sides))
           for k in range(sides)]
    return [(*pts[k], *pts[(k + 1) % sides]) for k in range(sides)]


def _chain(x, y, bonds=4):
    """A zigzag of bonds, as a carbon chain is drawn."""
    pts = [(x + 12.1 * k, y + (7.0 if k % 2 else 0.0)) for k in range(bonds + 1)]
    return [(*pts[k], *pts[k + 1]) for k in range(bonds)]


def _arrow(x0, x1, y):
    return [(x0, y, x1, y), (x1 - 4, y - 2.5, x1, y), (x1 - 4, y + 2.5, x1, y)]


def _word(x, y, text):
    return ((x, y - 8, x + 5.5 * len(text), y + 2), text)


def _find(segments, words=(), texts=(), avoid=None):
    return schemes.find_regions(list(segments), list(words), list(texts), 612, 792, avoid)


def test_a_ring_is_a_structure_and_takes_the_atoms_written_on_it():
    oh = _word(215, 196, "OH")
    (box,) = _find(_ring(200, 200) + [(212.1, 193, 216, 193)], [oh])
    assert box[0] < 188.5 and box[2] >= oh[0][2] and box[1] < 186.5 and box[3] > 213.5


def test_a_chain_is_a_structure_only_with_an_atom_other_than_carbon_on_it():
    chain = _chain(100, 300)
    assert _find(chain) == []                                      # a zigzag alone: a resistor, a spring, a border
    assert _find(chain, [_word(150, 304, "R1")]) == []             # a part's name in a circuit is no atom
    assert len(_find(chain, [_word(150, 304, "OH")])) == 1
    assert len(_find(chain, [_word(150, 304, "NH2")])) == 1


def test_line_art_that_is_not_chemistry_is_left_alone():
    box = [(100, 100, 200, 100), (200, 100, 200, 160), (200, 160, 100, 160), (100, 160, 100, 100)]
    diamond = [(300, 100, 310, 110), (310, 110, 300, 120), (300, 120, 290, 110), (290, 110, 300, 100)]
    hatching = [(400 + 4 * k, 100, 410 + 4 * k, 114) for k in range(12)]   # one direction, however many strokes
    assert _find(box + diamond + hatching, [_word(120, 130, "No"), _word(296, 112, "OK")]) == []
    assert _find(_ring(200, 200), avoid=[(150, 150, 250, 250)]) == []       # inside a table: the table's


def test_structures_joined_by_an_arrow_are_one_scheme_and_a_row_of_compounds_is_not():
    left, right = _ring(150, 300), _ring(330, 300)
    apart = _find(left + right)
    assert len(apart) == 2
    (scheme,) = _find(left + right + _arrow(190, 290, 300))
    assert scheme[0] < 140 and scheme[2] > 340
    # A plus sign set as text joins them as well
    assert len(_find(_ring(150, 500) + _ring(215, 500), [_word(180, 503, "+")])) == 1


def test_a_small_piece_beside_an_arrow_belongs_to_the_scheme():
    """A fragment too small to be told from other line art on its own (two bonds and a charge) is still part of the
    reaction it stands in."""
    fragment = [(300, 300, 312, 307), (312, 307, 324, 300)]
    (scheme,) = _find(_ring(150, 300) + _arrow(190, 280, 300) + fragment)
    assert scheme[2] >= 324


def test_a_name_under_it_is_taken_and_a_heading_a_sentence_and_a_caption_are_not():
    ring = _ring(200, 300)
    name = ((180, 318, 232, 328), "Cyclohexane", False)
    heading = ((150, 268, 330, 284), "Six-membered rings", True)
    sentence = ((150, 318, 520, 330), "A ring of six carbons is drawn as a hexagon in every scheme here.", False)
    caption = ((150, 318, 240, 328), "Scheme 2: a ring", False)
    (with_name,) = _find(ring, texts=[name])
    assert with_name[3] >= 328
    for other in (heading, sentence, caption):
        (alone,) = _find(ring, texts=[other])
        assert alone[1] > 284 and alone[3] < 318, other[1]


def _scheme_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 150), "The alcohol is made from the ketone in one step.", fontsize=11, fontname="helv")
    shape = page.new_shape()
    for x0, y0, x1, y1 in _ring(150, 300) + _ring(330, 300) + _arrow(190, 290, 300) + [(162.1, 293, 172, 287), (342.1, 293, 352, 287)]:
        shape.draw_line((x0, y0), (x1, y1))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    page.insert_text((173, 289), "O", fontsize=9, fontname="helv")
    page.insert_text((353, 289), "OH", fontsize=9, fontname="helv")
    page.insert_text((228, 294), "NaBH4", fontsize=8, fontname="helv")
    page.insert_text((100, 400), "The product is purified by distillation before use.", fontsize=11, fontname="helv")
    doc.save(path)
    return path


def test_a_drawn_scheme_is_tagged_as_one_figure_with_its_labels_inside(tmp_path):
    src = _scheme_pdf(tmp_path / "s.pdf")
    analysis = prepare_analysis(src)
    (figure,) = [i for i in analysis.pages[0].images if not i.decorative]
    assert figure.holds_pieces and figure.drawing == "scheme"
    assert set(figure.labels.split()) == {"O", "OH", "NaBH4"}
    assert sorted(b.text for b in analysis.pages[0].blocks) == [
        "The alcohol is made from the ketone in one step.", "The product is purified by distillation before use."]
    figure.alt = "A cyclic ketone is reduced with sodium borohydride to the alcohol."
    dst = tmp_path / "out.pdf"
    tag_document(src, dst, analysis, title="Scheme", language="en-US")
    assert integrity_errors(dst) == []
    tm = build_tagmap(dst)
    (tagged,) = [e for e in tm["elements"] if e["role"] == "Figure"]
    inside = [p for p in tm["pieces"] if p["el"] == tagged["i"]]
    assert {p["kind"] for p in inside} >= {"drawing", "text"}       # the bonds, and the atoms and reagent
    assert len([e for e in tm["elements"] if e["role"] == "P"]) == 2   # the two sentences, and no loose "O" or "OH"
    with fitz.open(src) as a, fitz.open(dst) as b:
        assert a[0].get_pixmap(dpi=72).samples == b[0].get_pixmap(dpi=72).samples


def test_whoever_describes_it_is_told_it_is_a_scheme(tmp_path):
    from app.pipeline.enrichment import _context

    analysis = prepare_analysis(_scheme_pdf(tmp_path / "s.pdf"))
    page = analysis.pages[0]
    (figure,) = [i for i in page.images if not i.decorative]
    context = _context(page, figure)
    assert "reaction scheme" in context and "NaBH4" in context


def _hairline(x0, x1, y):
    """An arrow's shaft drawn as a rectangle half a point high, as drawing programs set it, with its head."""
    return fitz.Rect(x0, y - 0.25, x1, y + 0.25), [(x1 - 5, y - 2.5, x1, y), (x1 - 5, y + 2.5, x1, y)]


def test_an_arrow_drawn_close_to_a_structure_still_joins_it_to_the_next(tmp_path):
    """The arrow's shaft was a hairline rectangle and its head lay within reach of the first structure's bonds, so
    there was nothing between the two structures to join them: a reaction's start was described without its end."""
    left = _chain(100, 300) + [(148.4, 300, 160, 307)]
    right = _chain(215, 300)
    shaft = (163.0, 303.0, 198.0, 303.0)
    head = [(193, 300.5, 198, 303), (193, 305.5, 198, 303)]
    atoms = [_word(88, 304, "HO"), _word(266, 304, "OH")]
    assert len(_find(left + right, atoms)) == 2                          # nothing between them: two compounds
    assert len(_find(left + right + [shaft] + head, atoms)) == 1         # the arrow gathered with the first one
    # A long bond out to a group is not an arrow: it starts at an atom, where another bond turns away from it
    long_bond = (148.4, 300, 176, 300)
    assert len(_find(_chain(100, 300) + [long_bond] + _chain(215, 300), atoms + [_word(178, 303, "OH")])) == 2
    # Through the page analysis: a hairline rectangle is a stroke
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()
    for x0, y0, x1, y1 in left + right + head:
        shape.draw_line((x0, y0), (x1, y1))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.draw_rect(fitz.Rect(163, 302.75, 198, 303.25))
    shape.finish(fill=(0, 0, 0), color=None)
    shape.commit()
    page.insert_text((86, 304), "HO", fontsize=9, fontname="helv")
    page.insert_text((266, 304), "OH", fontsize=9, fontname="helv")
    doc.save(tmp_path / "a.pdf")
    figures = [i for i in prepare_analysis(tmp_path / "a.pdf").pages[0].images if i.drawing == "scheme"]
    assert len(figures) == 1 and figures[0].bbox[0] < 100 and figures[0].bbox[2] > 260


def test_a_lone_rule_between_two_structures_does_not_join_them():
    rule = [(188.0, 310.0, 222.0, 310.0)]   # under a word, or the edge of something: no head, no arrow
    assert len(_find(_ring(150, 300) + _ring(260, 300) + rule)) == 2
    assert len(_find(_ring(150, 300) + _ring(260, 300) + _arrow(188, 222, 310))) == 1   # with a head, it is one


def test_a_line_of_labels_that_belongs_mostly_to_the_next_structure_stays_out():
    """"OH" of one structure and "CH3" of the next, set as one line of text, pulled the second structure into the
    first one's figure."""
    left, right = _ring(150, 300), _ring(300, 300)
    spanning = ((160, 280, 305, 290), "OH CH3", False)
    assert len(_find(left + right, texts=[spanning])) == 2


def test_a_letter_that_is_also_a_word_is_no_sign_of_chemistry():
    chain = _chain(100, 300)
    for word in ("I", "B", "P", "K"):
        assert _find(chain, [_word(150, 304, word)]) == [], word
    assert len(_find(chain, [_word(150, 304, "Cl")])) == 1


def test_a_picture_far_larger_than_a_figure_is_not_one_of_its_pieces():
    """On a flattened page the whole page is one image. Its middle fell inside the scheme drawn mid-page, so it was
    tagged as a piece of that figure: the figure's box became the page, and nothing marked the scheme itself."""
    from app.pipeline.models import ImageRegion, PageAnalysis
    from app.tagging.targets import ARTIFACT_LAYOUT, PageResolver

    scheme = ImageRegion(id="p0s0", page=0, bbox=(74, 351, 520, 481), xref=0, kind="vector", holds_pieces=True)
    page = PageAnalysis(number=0, width=595, height=842, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 842), images=[scheme])
    resolve = PageResolver(page)
    assert resolve("image", (0, 0, 595, 842), 7) == ARTIFACT_LAYOUT           # the page's own image
    assert resolve("image", (74, 351, 520, 481), 8) == ("p0s0", "Figure")       # the picture cut for the figure
    assert resolve("image", (300, 400, 300.4, 400.4), 0) == ("p0s0", "Figure")  # a speck inside it, as before
    assert resolve("image", (0, 200, 595, 640), 9) == ARTIFACT_LAYOUT           # a band across the page: the same


def test_a_structure_inside_a_scheme_that_grew_around_it_is_not_a_second_figure():
    """Three rings in a row joined by arrows, and a fourth under the middle one: the fourth's box meets the scheme
    only once the scheme has grown to its full width. It was left as a figure inside the figure."""
    row = _ring(150, 300) + _arrow(172, 228, 300) + _ring(250, 300) + _arrow(272, 328, 300) + _ring(350, 300)
    under = _ring(250, 322)
    boxes = _find(row + under)
    assert len(boxes) == 1 and boxes[0][0] < 140 and boxes[0][2] > 360 and boxes[0][3] > 330


def test_a_schemes_description_has_room_for_its_steps(tmp_path):
    """A description was cut at 400 characters, mid-sentence, whatever it described: a scheme of several steps
    ended in "…" before its last compound, and the reviewer reported it as cut off."""
    import asyncio

    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats, enrich

    steps = " ".join(f"Step {n}: compound {n} is treated with reagent {chr(64 + n)} in a solvent and gives compound {n + 1} "
                     f"after about {n + 1} hours." for n in range(1, 9))
    assert 700 < len(steps) < 900
    asked = []

    async def describe(long):
        async with MockProvider() as ai:
            async def fake(prompt, images, **kw):
                asked.append(prompt)
                return {"decorative": False, "kind": "diagram", "alt": steps, "latex": None}
            ai.chat_json = fake
            return await ai.describe_image(b"", 0, "", long=long) if long else await ai.describe_image(b"", 0, "")

    whole = asyncio.run(describe(True))["alt"]
    assert whole == steps and "max ~700 characters" in asked[-1]
    cut = asyncio.run(describe(False))["alt"]
    assert len(cut) <= 401 and cut.endswith("…") and "max ~250 characters" in asked[-1]   # a photograph's, as before
    assert "ending on a complete sentence" in asked[-1]

    # The page analysis's scheme is the one described at length; an ordinary picture is not
    src = _scheme_pdf(tmp_path / "s.pdf")
    analysis = prepare_analysis(src)
    calls = []

    class Recording(MockProvider):
        async def describe_image(self, png, page, context, long=False):
            calls.append(long)
            return {"decorative": False, "kind": "diagram", "alt": "A ketone is reduced to an alcohol.", "latex": None}

    async def run():
        async with Recording() as ai:
            return await enrich(ai, src, analysis, stats=EnrichmentStats())

    asyncio.run(run())
    assert calls == [True]
