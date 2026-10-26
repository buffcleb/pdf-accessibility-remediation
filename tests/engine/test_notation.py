"""E1: the AI reads specialist notation the layout rules can't, only if its reading matches the page,
and every such reading is flagged for review."""
import asyncio

import pytest

from pathlib import Path

from app.pipeline.fonts import _CANDIDATES
from app.pipeline.models import TextBlock
from app.pipeline.notation import consistent, need
from tests.engine.layout import draw_pdf

FONT = next((f for f in _CANDIDATES if Path(f).is_file()), None)
needs_font = pytest.mark.skipif(FONT is None, reason="no font with math symbols to embed")


def _page_with(path, lines):
    """A page with (x, y, text) lines in an embedded font that has math symbols (∑, ∫)."""
    return draw_pdf(path, lines, width=595, height=842, fontfile=FONT)


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


def test_what_is_spoken_keeps_every_number_and_unit():
    """Readings that match the page in their transcription and are wrong when spoken. The changed unit, the added
    carbon and the misread angle are kinds of error seen on real documents; the text here is made up."""
    from app.pipeline.notation import spoken_consistent

    # A unit changed, in a dosing guideline (read from a scan)
    ok, why = spoken_consistent("3. Target level: 40 – 90 mcg/mL, keep the dose under 30 mg/kg/day.",
                                "3. Target level: 40 minus 90 meg per milliliter, keep the dose under 30 "
                                "milligram per kilogram per day.")
    assert not ok and "mcg" in why
    # An answer dropped, taking its formula with it
    ok, why = spoken_consistent("d) H+, NO3 - e) Na+, NO3", "e); Na plus; NO3 minus")
    assert not ok and "3" in why
    # A carbon added
    ok, why = spoken_consistent("HS CH3", "H S C C H 3")
    assert not ok and "spells out C" in why
    # A division nobody wrote
    ok, why = spoken_consistent(".012 ± .003 (0.30 ± 0.08) × 30°", "0.012 plus or minus 0.003 divided by parentheses "
                                "0.30 plus or minus 0.08 close parentheses times 30 degrees")
    assert not ok and "division" in why
    # A digit misread from the picture
    assert not spoken_consistent("≈ 3 cos(40t - 215°)", "approximately equal to 3 times the cosine of 40t minus "
                                 "125 degrees")[0]


def test_what_is_spoken_may_say_numbers_and_units_in_words():
    from app.pipeline.notation import spoken_consistent

    for page, spoken in [
        ("H2SO4", "H two S O four"),
        ("H2O", "H sub 2 O"),
        ("x2 + 1", "x squared plus 1"),
        ("1/2", "one half"),
        ("7.5 mcg of the tablet", "7.5 micrograms of the tablet"),
        ("300 mg/200 ml at 12 ml/hr", "300 milligrams per 200 milliliters at 12 milliliters per hour"),
        ("4 mg/ml", "4 mg per ml"),
        ("60-120 mg/day", "sixty to one hundred twenty milligrams per day"),
        ("0.2-0.4 mg/day", "zero point two to zero point four milligrams per day"),
        ("15,000 units", "15000 units"),
        (".030 – .040", "0.03 to 0.04"),
        ("ΔH°298 K = - 92.4 kJ mol-1", "Delta H at 298 K equals minus 92.4 kilojoules per mole"),
        ("a/b", "a divided by b"),
        ("Fe2+ + 2e– → Fe", "F e two plus plus 2 electrons yields F e"),
    ]:
        assert spoken_consistent(page, spoken) == (True, ""), (page, spoken)
    # A fraction set one over the other has no slash in its text: the layout rules' reading says it is one
    assert spoken_consistent("a b", "a divided by b", "fraction, a, over b, end fraction")[0]
    assert not spoken_consistent("a b", "a divided by b")[0]


def test_the_notation_inside_a_sentence_is_found():
    from app.pipeline.notation import in_a_sentence, spans

    def pieces(text, kind="chemistry"):
        return [text[a:b] for a, b in spans(text, kind)]

    sentence = "The technician mixed the sample with dilute acid, which gave small amounts of aqueous H2O2."
    assert pieces(sentence) == ["H2O2"] and in_a_sentence(sentence, spans(sentence, "chemistry"))
    # Formulas joined by plus signs and arrows are one reaction, the plain members of it included
    assert pieces("What changes when HBr gas is removed? C2H6(g) + Br2(g) ↔ C2H5Br(l) + HBr(g)") == [
        "C2H6(g) + Br2(g) ↔ C2H5Br(l) + HBr(g)"]
    assert pieces("(a) 2SO3(g) ↔ 2SO2(g) + O2(g) and the heat is 198 kJ") == ["2SO3(g) ↔ 2SO2(g) + O2(g)"]
    assert pieces("the ions Fe2+ and Fe3+ are oxidized by KMnO4 in acid") == ["Fe2+", "Fe3+", "KMnO4"]
    # Ordinary words that happen to be element symbols are not formulas
    assert pieces("In the lab, As a rule, No one may eat") == []
    # A block that is all formula is read whole, as before
    whole = "2SO3(g) ↔ 2SO2(g) + O2(g)"
    assert not in_a_sentence(whole, spans(whole, "chemistry"))
    dose = "The stock strength is 4 mg/ml. How much of the liquid should be drawn up?"
    assert pieces(dose, "medical") == ["4 mg/ml"] and in_a_sentence(dose, spans(dose, "medical"))
    assert spans("the norm of x is less than 1", "math") == []


def test_list_marks_in_a_symbol_font_are_not_sent():
    def block(text, role="P"):
        return TextBlock(id="b", page=0, bbox=(0, 0, 10, 10), text=text, font_size=10, role=role)

    assert need(block(" To keep the workshop tidy.  To label every sample.")) is None
    assert need(block(" Yes")) is None
    assert need(block("x =  + 1 for all n")).kind == "unreadable"  # inside the line: something to read
    assert need(block(" x", role="Formula")).kind == "unreadable"


@pytest.mark.asyncio
async def test_only_the_formula_in_a_sentence_goes_to_the_ai(tmp_path):
    """The sentence's words stay as printed; the AI is asked about "H2O2" once, however often it appears."""
    import pymupdf as fitz
    from app.pipeline.enrichment import EnrichmentStats, _notation
    from app.pipeline.models import DocumentAnalysis, PageAnalysis

    path = tmp_path / "s.pdf"
    doc = fitz.open()
    page = doc.new_page()
    lines = ["The first batch held a little aqueous H2O2 after mixing.",
             "The second batch held more H2O2 once it was warmed."]
    blocks = []
    for i, line in enumerate(lines):
        y = 100 + 40 * i
        page.insert_text((72, y), line, fontsize=11)
        blocks.append(TextBlock(id=f"b{i}", page=0, bbox=(70, y - 12, 540, y + 4), text=line, font_size=11,
                                has_scripts=True))
    doc.save(path)
    analysis = DocumentAnalysis(page_count=1, title=None, language=None, has_struct_tree=False, is_marked=False, pages=[
        PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792), blocks=blocks)])

    class Reader:
        asked = []

        async def read_notation(self, png, page, extracted, kind):
            self.asked.append(extracted)
            return {"kind": "chemistry", "transcription": extracted, "latex": None, "spoken": "H 2 O 2",
                    "confidence": 0.9}

    stats = EnrichmentStats()
    await _notation(Reader(), fitz.open(path), analysis, stats)
    assert Reader.asked == ["H2O2"]
    assert blocks[0].actual_text == "The first batch held a little aqueous H 2 O 2 after mixing."
    assert blocks[1].actual_text == "The second batch held more H 2 O 2 once it was warmed."
    assert len(stats.notation) == 2 and stats.notation_rejected == 0


def test_a_reaction_with_a_symbol_font_arrow_is_one_piece():
    from app.pipeline.notation import spans

    text = "7. What forms when magnesium is heated in chlorine? Mg(s) + Cl2(g) à MgCl2(s)"
    assert [text[a:b] for a, b in spans(text, "unreadable")] == ["Mg(s) + Cl2(g) à MgCl2(s)"]
    # An unreadable character outside any formula: the block is read whole, as before
    assert spans("the value  is given by H2O", "unreadable") == []


def test_list_items_side_by_side_are_read_one_by_one():
    from app.pipeline.enrichment import _notation_regions
    from app.pipeline.models import PageAnalysis

    answers = [TextBlock(id=f"a{i}", page=0, bbox=(72 + 150 * i, 100, 210 + 150 * i, 114),
                         text=f"{'abc'[i]}. {i + 3} electrons; 2 H2O", font_size=11, role="LI", has_scripts=True)
               for i in range(3)]
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792), blocks=answers)
    assert [len(g) for g in _notation_regions(page)] == [1, 1, 1]


def test_a_formula_is_placed_in_the_rules_reading_of_its_sentence():
    """The rest of the line keeps what the layout rules made of it: its powers of ten stay powers."""
    from app.pipeline.notation import place_in_reading, spans

    text = "b) 3.01 x 1023 molecules of K2CO3 and 3.01 x 1024 molecules of NH3"
    reading = ("b) 3.01 x 10 to the power of 23 molecules of K sub 2 CO sub 3 and 3.01 x 10 to the power of 24 "
               "molecules of NH sub 3")
    out = reading
    for a, b in sorted(spans(text, "chemistry"), reverse=True):
        x, y = place_in_reading(text, reading, a, b)
        out = out[:x] + "[" + text[a:b] + "]" + out[y:]
    assert out == "b) 3.01 x 10 to the power of 23 molecules of [K2CO3] and 3.01 x 10 to the power of 24 molecules of [NH3]"
    assert place_in_reading("abc", "abc", 1, 2) == (1, 2)


def test_doses_are_put_in_words_by_rule():
    from app.pipeline.notation import say_doses, spoken_consistent

    cases = {
        "Give 150 mg IV q 8 hr. The stock strength is 4 mg/ml.":
            "Give 150 milligrams IV every 8 hours. The stock strength is 4 milligrams per milliliter.",
        "0.2 mg PO TID up to 0.4 mg PO TID (0.008 mg/kg/day in two doses, at most 1 mg/day).":
            "0.2 milligrams by mouth three times a day up to 0.4 milligrams by mouth three times a day (0.008 "
            "milligrams per kilogram per day in two doses, at most 1 milligram per day).",
        "Target level: 40 – 90 mcg/mL": "Target level: 40 to 90 micrograms per milliliter",
        "Work out the gtts/min for 20 gtt/ml tubing.": "Work out the drops per minute for 20 drops per milliliter tubing.",
        "give 7.5 mcg p.o. b.i.d. prn": "give 7.5 micrograms by mouth twice a day as needed",
        "What is the rate in mcg/kg/min at 12 ml/hr?": "What is the rate in micrograms per kilogram per minute at 12 milliliters per hour?",
    }
    for page, said in cases.items():
        assert say_doses(page) == said
        assert spoken_consistent(page, said) == (True, "")  # every number and unit is still heard
    # Words that only look like shorthand, and numbers that aren't a range, are left alone
    for same in ("the bid was accepted on 2020-10-11", "see the po box", "call 555-0100 or read pages 3/4"):
        assert say_doses(same) == same


def test_a_formula_is_said_one_way():
    from app.pipeline.notation import one_style, say_chemistry

    for heard in ("H two S O four", "H sub 2 S O sub 4", "H₂SO₄", "H2SO4", "H 2 S O 4"):
        assert say_chemistry(heard) == "H 2 S O 4"
    assert say_chemistry("Zn Br 2") == "Z n B r 2"
    assert say_chemistry("(NH4)2Cr2O7") == "open parenthesis N H 4 close parenthesis 2 C r 2 O 7"
    assert say_chemistry("Fe3O4(s) + 4H2(g) → 3Fe(s) + 4H2O(g)") == (
        "F e 3 O 4 solid plus 4 H 2 gas yields 3 F e solid plus 4 H 2 O gas")
    assert say_chemistry("N2(g) + 3H2(g) ↔ 2NH3(g)") == "N 2 gas plus 3 H 2 gas is in equilibrium with 2 N H 3 gas"
    assert say_chemistry("H 2 plus O 2 arrow H 2 O 2") == "H 2 plus O 2 yields H 2 O 2"
    assert say_chemistry("N H 3 open parenthesis a q close parenthesis plus H 2 O open parenthesis l close parenthesis") == (
        "N H 3 aqueous plus H 2 O liquid")
    # Ordinary words that are also element symbols stay words
    assert say_chemistry("In contrast, H 2 O 2 is green. As a rule He said No.") == (
        "In contrast, H 2 O 2 is green. As a rule He said No.")
    assert one_style("CO₂; mixes; into; the; cold; solvent; to; start", "chemistry") == (
        "C O 2 mixes into the cold solvent to start")
    assert one_style("x squared plus 1", "math") == "x squared plus 1"


@pytest.mark.asyncio
async def test_dosage_blocks_need_no_ai(tmp_path):
    import pymupdf as fitz
    from app.pipeline.enrichment import EnrichmentStats, _notation
    from app.pipeline.models import DocumentAnalysis, PageAnalysis

    block = TextBlock(id="b0", page=0, bbox=(70, 88, 540, 104), font_size=11,
                      text="The starting dose is 0.008 mg/kg/day, at most 1 mg/day.")
    analysis = DocumentAnalysis(page_count=1, title=None, language=None, has_struct_tree=False, is_marked=False, pages=[
        PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792), blocks=[block])])
    path = tmp_path / "d.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(path)

    class NeverAsked:
        async def read_notation(self, *a):
            raise AssertionError("a dose was sent to the AI")

    stats = EnrichmentStats()
    await _notation(NeverAsked(), fitz.open(path), analysis, stats)
    assert block.actual_text == "The starting dose is 0.008 milligrams per kilogram per day, at most 1 milligram per day."
    assert [n.get("by_rule") for n in stats.notation] == [True]


def test_the_detector_on_cases_from_real_documents():
    def kind(text, role="P", scripts=False):
        n = need(TextBlock(id="b", page=0, bbox=(0, 0, 10, 10), text=text, font_size=10, role=role, has_scripts=scripts))
        return n.kind if n else None

    # Prescription shorthand in capitals without dots, as a clinical guideline writes it
    assert kind("Administer the tablet 0.2 mg TID for 5 days") == "medical"
    assert kind("Take with food PRN.") == "medical"
    assert kind("One tablet 20 mg PO daily") == "medical"
    assert kind("Send it to PO Box 12, quoting PO 4411") is None
    # Reactions written as running text, between molecules of one element or with a leading coefficient
    assert kind("(c) H2(g) + I2(g) ↔ 2HI(g)") == "chemistry"
    assert kind("(d) 2SO3(g) ↔ 2SO2(g) + O2(g)") == "chemistry"
    assert kind("x → y as n grows") is None
    # Supply and signal names on a circuit are not formulas
    assert kind("VOUT1 vs VDD2", scripts=True) is None
    assert kind("IDD2 vs VDD2", scripts=True) is None
    assert kind("the supply VIN2 and VSS1", scripts=True) is None
    assert kind("1 VDD 2 ON1 and ALERT1 (Joined Inside the Part)", scripts=True) is None
    assert kind("the CCl4 layer and H2O2", scripts=True) == "chemistry"
    # A bar set before its letter is a conjugate; a broken accent inside a word is not math
    assert kind("a) f(z) = ¯z") == "math"
    assert kind("the opening of the song Rˆeverie") is None
    # A root sign with no Unicode value comes first in its line, like a list mark, but math follows it
    assert kind("� g/l t) + C cos(") == "unreadable"
    assert kind("� 1, −N ≤ k ≤ N ←→ (sin[ω(2N + 1)/2])/sin(ω/2) 0, otherwise") == "unreadable"


def test_doses_in_table_cells_are_put_in_words():
    from app.pipeline.models import PageAnalysis, TableCell, TableRegion
    from app.pipeline.notation import doses_in_cells

    cells = [TableCell(row=0, col=0, bbox=None, text="Drug"), TableCell(row=0, col=1, bbox=None, text="Dose"),
             TableCell(row=1, col=0, bbox=None, text="Tablet A"),
             TableCell(row=1, col=1, bbox=None, text="2.5 mg BID to 20 mg BID")]
    table = TableRegion(id="t0", page=0, bbox=(0, 0, 10, 10), n_rows=2, n_cols=2, cells=cells)
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792), tables=[table])
    assert doses_in_cells(page) == 1
    assert cells[3].actual_text == "2.5 milligrams twice a day to 20 milligrams twice a day"
    assert cells[2].actual_text is None


def test_words_broken_over_two_lines_are_whole_in_a_reading():
    from app.pipeline.notation import join_broken_words

    assert join_broken_words("to speed up extrac- tion, and in- crease the yield") == (
        "to speed up extraction, and increase the yield")
    assert join_broken_words("pre- and post-test, 5- to 10-fold, x - y") == "pre- and post-test, 5- to 10-fold, x - y"


def test_the_word_before_an_equation_and_its_number_are_set_apart():
    """A signals textbook: half of its readings were refused, mostly for leaving out "(3.12)" or "where", or for
    writing "from ... to" where the limits go."""
    from app.pipeline.notation import around_an_equation, with_its_surroundings

    assert around_an_equation("y[n] = G(ejΩ)ejΩn (3.12)") == ("", "y[n] = G(ejΩ)ejΩn", "3.12")
    assert around_an_equation("where ∫∞ G(s) = g(τ)e−sτ dτ (3.4)") == ("where", "∫∞ G(s) = g(τ)e−sτ dτ", "3.4")
    assert around_an_equation("w[k]e−jΩk (3.27)") == ("", "w[k]e−jΩk", "3.27")
    assert around_an_equation("see the table (2)") == ("", "see the table (2)", "")
    assert around_an_equation("where the limit is taken") == ("", "where the limit is taken", "")

    lead, core, label = around_an_equation("where ∫∞ G(s) = g(τ)e−sτ dτ (3.4)")
    assert consistent(core, "G(s) = ∫_{-∞}^{∞} g(τ)e^{-sτ} dτ")[0]
    assert with_its_surroundings("G of s equals the integral of g of tau", lead, label) == (
        "where G of s equals the integral of g of tau; equation 3.4")
    assert with_its_surroundings("where x is 1; equation 3.4", "where", "3.4") == "where x is 1; equation 3.4"
    # "from" and "to" written where the limits go print nothing
    assert consistent("∑∞ W(ejΩ) =", "W(e^jΩ) = ∑ from n = -∞ to ∞")[0]
    assert not consistent("a = b", "a = b from here to there and more")[0]
