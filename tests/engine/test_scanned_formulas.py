"""Formulas on scanned pages (#183): OCR text that looks like a mangled formula goes to the AI as notation; only on
pages where some does, the AI is asked once where the page's formulas are, and those are read too. A scanned page of
prose costs no extra call."""
import asyncio

import pymupdf as fitz
import pytest

from app.pipeline.models import TextBlock
from app.pipeline.notation import consistent_scanned, need, scanned_math

GARBLED = "y ( t ) = 8 ( t - 1 ) u ( t + 1 )"   # how OCR leaves a typeset equation: letters, signs and brackets apart
PROSE = "Use the plot to estimate the steady state output for the following inputs."


def _b(text, conf, role="P"):
    return TextBlock(id="b", page=0, bbox=(100, 100, 300, 120), text=text, font_size=11, role=role, ocr_conf=conf)


@pytest.mark.parametrize("text,conf,flagged", [
    (GARBLED, 40, True),
    ("f ( x ) = | e - 2 x dx = - e |", 55, True),
    (GARBLED, 90, False),                     # read with confidence: it's what is printed
    (GARBLED, None, False),                   # no confidence to go by: no extra AI call
    (PROSE, 50, False),                       # unsure prose (handwriting) is still words
    ("2 c flour + 1 t salt , 3 eggs", 60, False),  # a recipe's quantities: few operators
    ("A B C D E F G", 30, False),             # stray letters (sheet music read as text): no operators
    ("= 1", 20, False),                       # too little to read on its own
])
def test_what_counts_as_a_garbled_formula(text, conf, flagged):
    assert scanned_math(_b(text, conf)) is flagged


def test_a_scanned_formula_is_notation_whatever_role_its_size_gave_it():
    tall = _b(GARBLED, 40, role="H2")       # a tall formula can be taken for a heading by its size
    assert need(tall).kind == "math" and "garbled" in need(tall).reason
    found = _b("y = 2 sin ( 3t )", 92)
    found.scan_formula = True
    assert need(found).kind == "math" and "found by the AI" in need(found).reason
    assert need(_b(GARBLED, 40, role="Artifact")) is None


def test_a_scanned_reading_is_anchored_to_what_ocr_read():
    assert consistent_scanned(GARBLED, "y(t) = δ(t − 1) u(t + 1)")[0]          # δ for the 8 OCR made of it
    ok, why = consistent_scanned(GARBLED, "z = 42")
    assert not ok and "characters OCR read" in why
    ok, why = consistent_scanned("x = 1", "x = 1 " + "and a great deal more that was never on the page " * 3)
    assert not ok and "longer" in why
    ok, why = consistent_scanned("s e m i s", "semis")
    assert not ok and "no formula" in why


def test_the_formula_finder_keeps_only_boxes_that_make_sense():
    from app.ai.base import ChatResult, TokenUsage
    from app.ai.mock import MockProvider

    class Boxes(MockProvider):
        async def _chat(self, model, system, prompt, images):
            import json
            return ChatResult(json.dumps({"formulas": [{"box": [100, 200, 600, 260]}, {"box": [5, 5, 6, 6]},
                                                       {"box": ["a", 1, 2, 3]}, [0, 900, 2000, 990], "nonsense"]}),
                              TokenUsage())

    async def go():
        async with Boxes() as ai:
            return await ai.find_formulas(b"png", 0)

    assert asyncio.run(go()) == [(0.1, 0.2, 0.6, 0.26), (0.0, 0.9, 1.0, 0.99)]


def _scanned_page(tmp_path):
    """A page drawn as text, then marked as a scan would be after OCR: each block with an OCR confidence."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 100), PROSE, fontsize=11, fontname="helv")
    page.insert_text((72, 160), GARBLED, fontsize=11, fontname="helv")
    page.insert_text((72, 220), "w ( t ) = 2 cos ( 3 t ) and more", fontsize=11, fontname="helv")
    page.insert_text((72, 280), "The last line is ordinary prose about the inputs again.", fontsize=11, fontname="helv")
    pdf = tmp_path / "scan.pdf"
    doc.save(pdf)
    from app.pipeline.orchestrator import prepare_analysis

    analysis = prepare_analysis(pdf)
    for b in analysis.pages[0].blocks:  # the first formula read with low confidence; the second, and the prose, well
        b.ocr_conf = 40.0 if b.text.startswith("y") else 95.0
    return pdf, analysis


def _run(pdf, analysis, monkeypatch, boxes):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich

    asked = []

    async def find(self, png, page):
        asked.append(page)
        return boxes

    monkeypatch.setattr(MockProvider, "find_formulas", find)

    async def go():
        async with MockProvider() as ai:
            return await enrich(ai, pdf, analysis)

    return asyncio.run(go()), asked


def test_a_flagged_page_is_asked_once_and_the_formulas_it_finds_are_read(tmp_path, monkeypatch):
    pdf, analysis = _scanned_page(tmp_path)
    page = analysis.pages[0]
    second = next(b for b in page.blocks if b.text.startswith("w"))
    assert scanned_math(next(b for b in page.blocks if b.text.startswith("y"))) and not scanned_math(second)
    # The AI's box (in thousandths of the page) around the formula the OCR text didn't give away
    box = (second.bbox[0] / 600 - 0.01, second.bbox[1] / 800 - 0.01, second.bbox[2] / 600 + 0.01, second.bbox[3] / 800 + 0.01)
    stats, asked = _run(pdf, analysis, monkeypatch, [box])
    assert asked == [0] and stats.scan_formula_pages == 1
    read = {n["text"][:1]: n for n in stats.notation}
    assert set(read) == {"y", "w"}, stats.findings
    assert "found by the AI" in read["w"]["reason"] and "garbled" in read["y"]["reason"]
    assert all(b.role == "Formula" for b in page.blocks if b.text[:1] in "yw")
    assert [b.role for b in page.blocks if b.text[:1] in "UT"] == ["P", "P"]   # the prose is left as it was


def test_a_scanned_page_of_prose_costs_no_extra_call(tmp_path, monkeypatch):
    pdf, analysis = _scanned_page(tmp_path)
    for b in analysis.pages[0].blocks:
        b.ocr_conf = 95.0      # all read with confidence: nothing garbled
    stats, asked = _run(pdf, analysis, monkeypatch, [(0.1, 0.1, 0.9, 0.2)])
    assert asked == [] and stats.scan_formula_pages == 0
    assert not [n for n in stats.notation if "scanned" in n["reason"]]


def test_a_scanned_reading_that_does_not_match_keeps_the_ocr_text(tmp_path, monkeypatch):
    from app.ai.mock import MockProvider

    pdf, analysis = _scanned_page(tmp_path)

    async def wrong(self, png, page, extracted, kind):
        return {"kind": "math", "transcription": "z = 42", "latex": "z=42", "spoken": "z equals 42", "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "read_notation", wrong)
    stats, _ = _run(pdf, analysis, monkeypatch, [])
    assert stats.notation == [] and stats.notation_rejected >= 1
    assert any("characters OCR read" in f for f in stats.findings)


def test_each_block_gets_the_mean_confidence_of_the_ocr_words_in_it():
    from types import SimpleNamespace

    from app.pipeline.ocr import OcrLine, OcrWord, block_confidence

    formula, prose, empty = _b(GARBLED, None), _b(PROSE, None), _b("figure", None)
    formula.bbox, prose.bbox, empty.bbox = (100, 100, 300, 120), (100, 200, 500, 220), (100, 400, 200, 420)
    line = lambda y, confs: OcrLine(page=0, text="", bbox=(100, y, 500, y + 20), origin=(100, y + 15), size=11,  # noqa: E731
                                    direction=(1, 0), extent=400,
                                    words=[OcrWord("w", c, (110 + 40 * i, y + 2, 140 + 40 * i, y + 18)) for i, c in enumerate(confs)])
    block_confidence([SimpleNamespace(number=0, blocks=[formula, prose, empty])], [line(100, [30, 50]), line(200, [96, 94, 95])])
    assert (formula.ocr_conf, prose.ocr_conf, empty.ocr_conf) == (40, 95, None)


def test_a_list_of_boxes_cut_off_at_the_output_limit_keeps_the_ones_written_in_full():
    from app.ai.base import ChatResult, TokenUsage
    from app.ai.mock import MockProvider

    class CutOff(MockProvider):
        async def _chat(self, model, system, prompt, images):
            return ChatResult('{"formulas": [[100, 200, 600, 260], [100, 300, 600, 360], [100, 200, 600, 260], [100, 4',
                              TokenUsage())

    async def go():
        async with CutOff() as ai:
            return await ai.find_formulas(b"png", 0)

    assert asyncio.run(go()) == [(0.1, 0.2, 0.6, 0.26), (0.1, 0.3, 0.6, 0.36)]   # the repeat said once
