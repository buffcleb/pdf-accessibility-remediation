"""E1: the AI reads specialist notation the layout rules can't, only if its reading matches the page,
and every such reading is flagged for review."""
import asyncio

import pymupdf as fitz
import pytest

from pathlib import Path

from app.pipeline.fonts import _CANDIDATES
from app.pipeline.models import TextBlock
from app.pipeline.notation import consistent, need

FONT = next((f for f in _CANDIDATES if Path(f).is_file()), None)
needs_font = pytest.mark.skipif(FONT is None, reason="no font with math symbols to embed")


def _page_with(path, lines):
    """A page with (x, y, text) lines in an embedded font that has math symbols (∑, ∫)."""
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_font(fontname="emb", fontfile=FONT)
    for x, y, text in lines:
        p.insert_text((x, y), text, fontsize=11, fontname="emb")
    doc.save(path)
    return path


def _b(text, role="Formula", scripts=False):
    return TextBlock(id="b", page=0, bbox=(100, 100, 300, 120), text=text, font_size=11, role=role,
                     has_scripts=scripts)


@pytest.mark.parametrize("text,role,scripts,kind", [
    ("x = \ufffd + 1", "Formula", False, "unreadable"),
    ("\ue046 y = 2", "Formula", False, "unreadable"),
    ("∑ x = 10", "Formula", False, "math"),
    ("√2 = 1.414", "Formula", False, "math"),
    ("v\u20d7 = (1, 2)", "Formula", False, "math"),
    ("H2SO4 + 2NaOH", "P", True, "chemistry"),
    ("CH4 + 2O2 → CO2 + 2H2O", "P", False, "chemistry"),
    ("Give 10 mg/kg/day in two doses.", "P", False, "medical"),
    ("Take 1 tablet p.o. b.i.d. with food.", "P", False, "medical"),
    ("R = 50 kΩ ± 5%", "Formula", False, "engineering"),
    ("∥F − G∥ ≲ 1", "Formula", False, "math"),
    ("x ∈ A ⇒ f(x) ≥ 0", "Formula", False, "math"),
])
def test_notation_the_rules_cannot_read_is_sent_to_the_ai(text, role, scripts, kind):
    n = need(_b(text, role, scripts))
    assert n is not None and n.kind == kind, (text, n)


@pytest.mark.parametrize("text,role,scripts", [
    ("10 squared = 100", "Formula", True),           # the rules read powers
    ("Page iv", "P", False),                         # a roman numeral, not "i.v."
    ("20 U.S.C. 1092(a)", "P", False),               # United States Code, not "s.c."
    ("The bid was accepted.", "P", False),           # an English word, not "b.i.d."
    ("f : A → B is a map", "P", True),               # math arrow, no chemical formula
    ("\ue046", "P", False),                          # a lone symbol glyph
    ("An ordinary sentence about Ω-shaped tubing.", "P", False),  # engineering symbols only count in formulas
])
def test_ordinary_text_is_left_to_the_rules(text, role, scripts):
    assert need(_b(text, role, scripts)) is None


def test_consistency_accepts_a_faithful_reading_and_rejects_invented_ones():
    assert consistent("H2SO4", "H₂SO₄")[0]  # subscripts are the same characters
    assert consistent("x = \ufffd + 1", "x = α + 1")[0]  # the unreadable glyph may become a symbol
    assert consistent("L = 1/n ∑ (Y − Y′)", "", r"L = \frac{1}{n} \sum (Y - Y')")[0]  # LaTeX commands don't count
    ok, why = consistent("x = 2 + 3", "x = 5")
    assert not ok and "missing" in why  # the AI solved it instead of reading it
    ok, why = consistent("a = b", "a = b + c + d + e + f")
    assert not ok and "aren't on the page" in why
    assert not consistent("x = 1", "")[0]
    # LaTeX the model slips into a plain transcription prints nothing; its Greek commands are letters
    assert consistent("\ufffd4a2/p\ufffd", "left(4a²/pright)")[0]
    assert consistent("\ufffd 1/(1 −σ|Fk|2)", r"\sqrt{1/(1 - \sigma |F_k|^2)}")[0]
    assert consistent("the left side", "the left side")[0]


@needs_font
def test_ai_reads_split_equations_once_and_flags_it(tmp_path):
    """End to end with the mock AI: two fragments of one displayed equation (one with an unreadable
    glyph) become one Formula with the AI's reading, which the review list flags."""
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich
    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document
    from app.verification.verify import VerifyOptions, verify_output

    pdf = _page_with(tmp_path / "eq.pdf", [
        (72, 80, "The result below holds for every case we tested in the study."),
        (200, 140, "x = 1/(1 − y)"),
        (200, 154, "∫ y dy = 2"),  # the second line of the same displayed equation
        (72, 220, "This paragraph comes after the equation and is ordinary prose.")])

    analysis = prepare_analysis(pdf)
    before = sum(len(pg.blocks) for pg in analysis.pages)

    async def go():
        async with MockProvider() as ai:
            return await enrich(ai, pdf, analysis)

    stats = asyncio.run(go())
    assert len(stats.notation) == 1, stats.findings
    read = stats.notation[0]
    assert read["spoken"].startswith("AI reading of") and read["pieces"] >= 1
    formula = next(b for pg in analysis.pages for b in pg.blocks if b.actual_text == read["spoken"])
    assert formula.role == "Formula"
    assert sum(len(pg.blocks) for pg in analysis.pages) == before - (read["pieces"] - 1)

    out = tmp_path / "tagged.pdf"
    tag_document(pdf, out, analysis, title="t", language="en-US")
    rep = asyncio.run(verify_output(out, VerifyOptions(
        ai=False, ai_read=[(n["page"], n["bbox"], n["kind"], n["spoken"]) for n in stats.notation])))
    flagged = [f for f in rep.findings if f.issue == "read-aloud text written by AI"]
    assert len(flagged) == 1 and flagged[0].role == "Formula" and flagged[0].element is not None
    alts = [e.get("alt") for e in rep.tagmap["elements"] if e["role"] == "Formula"]
    assert read["spoken"] in alts


@needs_font
def test_rejected_reading_keeps_the_rules(tmp_path, monkeypatch):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich
    from app.pipeline.orchestrator import prepare_analysis

    pdf = _page_with(tmp_path / "eq.pdf", [(72, 80, "Some ordinary introductory text for the page here."),
                                           (200, 140, "∑ k = 10 + 20")])

    async def wrong(self, png, page, extracted, kind_hint):
        return {"kind": "math", "transcription": "k = 30", "latex": None, "spoken": "k equals 30", "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "read_notation", wrong)
    analysis = prepare_analysis(pdf)

    async def go():
        async with MockProvider() as ai:
            return await enrich(ai, pdf, analysis)

    stats = asyncio.run(go())
    assert stats.notation == [] and stats.notation_rejected == 1
    assert any("not used" in f for f in stats.findings)
    assert not any(b.actual_text == "k equals 30" for pg in analysis.pages for b in pg.blocks)


@needs_font
def test_switched_off_per_job(tmp_path):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich
    from app.pipeline.orchestrator import prepare_analysis

    pdf = _page_with(tmp_path / "eq.pdf", [(200, 140, "∑ k = 10")])
    analysis = prepare_analysis(pdf)

    async def go():
        async with MockProvider() as ai:
            return await enrich(ai, pdf, analysis, notation=False)

    assert asyncio.run(go()).notation == []


@needs_font
def test_readings_survive_reanalysis_for_the_modified_file(tmp_path):
    """The modified output re-analyzes the document (after flattening); the AI's readings must carry
    over, joined fragments included, without asking the AI again."""
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich
    from app.pipeline.orchestrator import prepare_analysis
    from app.pipeline.transfer import transfer_enrichment

    pdf = _page_with(tmp_path / "eq.pdf", [
        (72, 80, "The result below holds for every case we tested in the study."),
        (200, 140, "x = 1/(1 − y)"),
        (200, 154, "∫ y dy = 2"),
        (72, 220, "This paragraph comes after the equation and is ordinary prose.")])
    analysis = prepare_analysis(pdf)

    async def go():
        async with MockProvider() as ai:
            return await enrich(ai, pdf, analysis)

    stats = asyncio.run(go())
    reading = stats.notation[0]
    again = prepare_analysis(pdf)  # what the modified file starts from
    transfer_enrichment(analysis, again, {0}, set(), notation=stats.notation)
    formulas = [b for b in again.pages[0].blocks if b.actual_text == reading["spoken"]]
    assert len(formulas) == 1 and formulas[0].role == "Formula"
    assert len(again.pages[0].blocks) == len(analysis.pages[0].blocks)
    assert all(bid in {b.id for b in again.pages[0].blocks} | {i.id for i in again.pages[0].images}
               for bid in again.pages[0].reading_order)


def test_markup_in_the_spoken_form_becomes_words():
    from app.ai.mock import MockProvider

    async def go():
        async with MockProvider() as ai:
            async def fake(prompt, images, **kw):
                return {"kind": "math", "transcription": "bF", "spoken": r"the norm of b_F_prime is x^2 \alpha {y}",
                        "confidence": 1}
            ai.chat_json = fake
            return await ai.read_notation(b"", 0, "bF", "math")

    assert asyncio.run(go())["spoken"] == "the norm of b sub F sub prime is x to the power of 2 alpha y"
