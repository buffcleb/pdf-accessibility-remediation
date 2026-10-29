"""A scanned form's small two-line label beside a typed value read as garbage: the AI, reading the label's first
line afresh, read its second line too, which squeezed the text into a sliver of the first line's place; the analyzer
then took that tiny OCR text for superscripts of the line below ("a to the power of b ..."), interleaving the two
labels letter by letter; and the dotted leader the value sits on was read as a long run of letters."""
import asyncio

import pymupdf as fitz

from app.config import get_settings
from app.pipeline import ocr
from app.pipeline.analyzer import _extract_blocks, _Run, _visual_lines
from app.pipeline.ocr import OcrLine, OcrWord
from app.pipeline.ocr_ai import correct_lines, without_neighbours


def _row(key, text, conf, x0, x1, y0=100, y1=108):
    return {"key": key, "text": text, "conf": conf, "bbox": (x0, y0, x1, y1), "visual": (x0, y0, x1, y1)}


def test_a_dotted_leader_read_as_letters_is_left_out(monkeypatch):
    rows = [_row((1, 1, 1), "Branch", 95.0, 40, 70), _row((1, 1, 1), "of", 93.0, 72, 80),
            _row((1, 1, 1), "A", 11.0, 260, 266), _row((1, 1, 1), "eeoseeeoaeeseeeoees", 0.0, 268, 345),
            _row((1, 1, 1), "Signed", 94.0, 400, 430)]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300: [dict(r) for r in rows])
    page = fitz.open().new_page(width=612, height=792)
    assert [ln.text for ln in ocr._tsv_lines(page, "eng")] == ["Branch of", "Signed"]


def test_unsure_words_that_arent_leaders_stay():
    assert not ocr._leader({"text": "Mississippi", "conf": 10.0})       # few letters, but a word's mix of them
    assert not ocr._leader({"text": "eeoseeeoaeeseeeoees", "conf": 80.0})  # read with confidence
    assert not ocr._leader({"text": "eeose", "conf": 0.0})               # too short to tell
    assert not ocr._leader({"text": "(2t+20°)", "conf": 5.0})            # handwritten math, read unsure
    assert ocr._leader({"text": "..........", "conf": 5.0})
    assert ocr._leader({"text": "__", "conf": 26.0})                    # a blank to fill in
    assert not ocr._leader({"text": "__", "conf": 90.0})
    assert not ocr._leader({"text": "-", "conf": 10.0})                   # may be the text's own


def test_only_letters_touching_a_leader_go_with_it(monkeypatch):
    rows = [_row((1, 1, 1), "__", 5.0, 200, 240), _row((1, 1, 1), "QX", 20.0, 250, 266),
            _row((1, 1, 1), "No.", 90.0, 270, 285), _row((1, 1, 1), "eeoseeeoaeeseeeoees", 0.0, 330, 400),
            _row((1, 1, 1), "B", 20.0, 440, 446)]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300: [dict(r) for r in rows])
    page = fitz.open().new_page(width=612, height=792)
    # A blank before it isn't a leader's start; a letter far along isn't the leader's either
    assert [ln.text for ln in ocr._tsv_lines(page, "eng")] == ["QX No.", "B"]


def test_a_label_whose_blank_runs_under_the_typed_value_ends_where_the_value_starts(monkeypatch):
    # "Kind of Service" with its blank read into the last word, and a value typed on the blank from x=118
    rows = [_row((1, 1, 1), "Kind", 96.0, 37, 60), _row((1, 1, 1), "of", 90.0, 63, 72),
            _row((1, 1, 1), "Service__x", 23.0, 75, 250)]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300: [dict(r) for r in rows])
    page = fitz.open().new_page(width=612, height=792)
    typed = [(160, 99, 200, 109), (118, 99, 150, 109), (205, 99, 294, 109)]  # the value's words
    lines = ocr._tsv_lines(page, "eng", existing=typed)
    assert [ln.text for ln in lines] == ["Kind of Service"] and lines[0].bbox[2] == 118
    # A word that isn't a blank's start is left as read
    rows[2]["text"] = "Services"
    assert [ln.text for ln in ocr._tsv_lines(page, "eng", existing=typed)] == ["Kind of Services"]


def test_a_reading_that_takes_in_the_next_line_is_cut_back():
    assert without_neighbours("Office or Branch Regional Section", ["Regional Section"]) == "Office or Branch"
    assert without_neighbours("Regional Section Office or Branch", ["Regionel Section"]) == "Office or Branch"
    assert without_neighbours("Office or Branch", ["Regional Section"]) == "Office or Branch"
    assert without_neighbours("Regional Section", ["Regional Section"]) == "Regional Section"  # all of it: kept


def _line(text, conf, bbox, size):
    words = text.split()
    step = (bbox[2] - bbox[0]) / len(words)
    ws = [OcrWord(w, conf, (bbox[0] + i * step, bbox[1], bbox[0] + (i + 1) * step, bbox[3])) for i, w in enumerate(words)]
    return OcrLine(0, text, bbox, (bbox[0], bbox[3]), size, (1, 0), bbox[2] - bbox[0], ws)


def test_the_ai_reading_of_a_label_leaves_out_the_line_below(tmp_path, monkeypatch):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats

    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(pdf)
    monkeypatch.setattr(get_settings(), "ocr_ai_transcribe_below", 45.0)
    first = _line("ffice or Bramch", 20.0, (37, 492, 112, 500), 7.0)
    second = _line("Regional Section", 97.0, (37, 500, 102, 508), 6.5)

    async def read(self, png, page, text, how):
        return {"text": "Office or Branch Regional Section", "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "correct_ocr_line", read)
    stats = EnrichmentStats()

    async def go():
        async with MockProvider() as ai:
            await correct_lines(ai, str(pdf), [first, second], stats)

    asyncio.run(go())
    assert first.text == "Office or Branch" and first.source == "ai-transcribed"
    assert second.text == "Regional Section"


def _runs(text, x, base, size, ocr, seq0=0):
    out = []
    for i, c in enumerate(text):
        char = {"bbox": (x + 0.5 * size * i, base - 0.8 * size, x + 0.5 * size * (i + 1), base + 0.2 * size),
                "c": c, "origin": (x + 0.5 * size * i, base)}
        r = _Run({"size": size, "flags": 0, "font": "DejaVuSans", "color": 0}, char, seq0 + i)
        r.ocr = ocr
        out.append(r)
    return out


def test_small_ocr_text_beside_a_line_is_a_line_of_its_own():
    # A label squeezed small just above the next line of the label, both from the OCR layer, with the typed value
    # (larger, printed) further along the second line
    small = _runs("Office or Branch", 37, 498.5, 3.5, True)
    below = _runs("Regional Section", 37, 506.5, 6.5, True, seq0=100)
    typed = _runs("Head Clerk", 350, 505.0, 12, False, seq0=200)
    lines = _visual_lines(small + below + typed)
    assert ["".join(r.text for r in ln.runs) for ln in lines] == ["Office or Branch", "Head Clerk", "Regional Section"]
    assert not any(r.script for ln in lines for r in ln.runs)
    # Printed (visible) text the same shape is still read as scripts, as before
    printed = _visual_lines(_runs("x", 72, 100.0, 12, False) + _runs("2", 78, 95.5, 8, False, seq0=5))
    assert len(printed) == 1 and printed[0].runs[1].script == "sup"


def test_the_ocr_layer_reads_without_powers(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((37, 499), "Office or Branch", fontsize=3.5, fontname="helv", render_mode=3)
    page.insert_text((37, 506.5), "Regional Section", fontsize=6.5, fontname="helv", render_mode=3)
    page.insert_text((350, 505), "Head Clerk", fontsize=12, fontname="tiro")
    path = tmp_path / "layer.pdf"
    doc.save(path)
    said = " ".join((b.actual_text or b.text) for b in _extract_blocks(fitz.open(path)[0], 0, ocr=True))
    assert "power" not in said and "Office or Branch" in said and "Regional Section" in said and "Head Clerk" in said
    # Invisible text that isn't the engine's OCR layer (a flattened page's, set glyph by glyph at the original sizes)
    # keeps its scripts
    assert "power" in " ".join((b.actual_text or b.text) for b in _extract_blocks(fitz.open(path)[0], 0))


def test_text_turned_up_a_margin_reads_in_order_up_the_page(monkeypatch):
    # "Kept on file by the office", printed up the left margin: Tesseract gives the words in reading order, each box
    # taller than wide, the first at the bottom
    words = [("Kept", 500, 520), ("on", 490, 497), ("file", 474, 487), ("by", 464, 471), ("the", 450, 461),
             ("office", 425, 447)]
    rows = [{"key": (1, 1, 1), "text": t, "conf": 95.0, "bbox": (8, y0, 15, y1), "visual": (8, y0, 15, y1)}
            for t, y0, y1 in words]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300: [dict(r) for r in rows])
    page = fitz.open().new_page(width=612, height=792)
    (line,) = ocr._tsv_lines(page, "eng")
    assert line.text == "Kept on file by the office" and line.direction == (0, -1)
    assert line.size < 10 and line.extent == 95                         # the margin's width, the words' run
    out = fitz.open()
    out.new_page(width=612, height=792)
    from app.pipeline.fonts import text_layer_font
    ocr.write_invisible_line(out[0], text_layer_font()[0], line.text, line.origin, line.size, line.direction,
                             line.extent)
    (written,) = [ln for b in out[0].get_text("dict")["blocks"] for ln in b["lines"]]
    assert written["dir"] == (0.0, -1.0)
    x0, y0, x1, y1 = written["bbox"]
    assert 6 <= x0 and x1 <= 17 and 420 <= y0 and y1 <= 522             # over the printed words


def test_a_value_typed_onto_a_scanned_form_is_not_a_heading(tmp_path):
    from app.pipeline.structure.headings import is_heading_candidate

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Name of the office supplying the goods", fontsize=7, fontname="helv", render_mode=3)
    page.insert_text((72, 140), "Northfield Paper Company", fontsize=14, fontname="tiro")   # typed in
    path = tmp_path / "form.pdf"
    doc.save(path)
    blocks = {b.text: b for b in _extract_blocks(fitz.open(path)[0], 0, ocr=True)}
    value = blocks["Northfield Paper Company"]
    assert value.typed_on_scan and not blocks["Name of the office supplying the goods"].typed_on_scan
    assert not is_heading_candidate(value, 7.0)
    # The same print on a page the engine didn't OCR is judged as before
    plain = {b.text: b for b in _extract_blocks(fitz.open(path)[0], 0)}["Northfield Paper Company"]
    assert not plain.typed_on_scan and is_heading_candidate(plain, 7.0)


def test_a_line_ocr_mostly_failed_on_is_not_a_heading_whatever_its_size():
    from app.pipeline.models import TextBlock
    from app.pipeline.structure.headings import is_heading_candidate

    block = TextBlock(id="b", page=0, bbox=(370, 350, 437, 370), text="Dote:", font_size=16.0, ocr_conf=12.0)
    assert not is_heading_candidate(block, 8.0)
    block.ocr_conf = 92.0                                                # read with confidence: a heading
    assert is_heading_candidate(block, 8.0)


def test_tall_noise_is_not_taken_for_turned_text(monkeypatch):
    def rows(words, conf):
        return [{"key": (1, 1, 1), "text": t, "conf": conf, "bbox": (x0, y0, x0 + 6, y1), "visual": (x0, y0, x0 + 6, y1)}
                for t, x0, y0, y1 in words]

    page = fitz.open().new_page(width=612, height=792)
    unsure = rows([("Kept", 8, 500, 520), ("office", 8, 425, 447)], 45.0)   # a score's notes read as letters
    apart = rows([("Kept", 8, 500, 520), ("office", 300, 425, 447)], 95.0)  # not one column
    single = rows([("Kept", 8, 500, 520)], 95.0)
    for found in (unsure, apart, single):
        monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300, found=found: [dict(r) for r in found])
        assert all(ln.direction == (1, 0) for ln in ocr._tsv_lines(page, "eng"))


def test_a_form_number_in_small_print_is_not_part_of_the_title_across_the_row():
    # Both from the OCR layer, their baselines nearly level (worked out from the boxes)
    number = _runs("Form 12 (Rev. 4/01)", 3, 314.6, 5.4, True)
    title = _runs("CERTIFICATE OF RECEIPT", 356, 317.2, 9.3, True, seq0=100)
    lines = _visual_lines(number + title)
    assert sorted("".join(r.text for r in ln.runs) for ln in lines) == ["CERTIFICATE OF RECEIPT", "Form 12 (Rev. 4/01)"]
    # Printed text in the same places is one row, as before
    printed = _visual_lines(_runs("Form 12", 3, 314.6, 5.4, False) + _runs("CERTIFICATE", 356, 317.2, 9.3, False, 50))
    assert len(printed) == 1


def test_a_label_and_the_value_typed_beside_it_are_two_blocks_read_label_first(tmp_path):
    from app.pipeline.models import PageAnalysis
    from app.pipeline.structure.reading_order import heuristic_reading_order

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((370, 466), "Kept on file", fontsize=12, fontname="tiro")                # typed first
    page.insert_text((37, 466.4), "Kind of Service", fontsize=7, fontname="helv", render_mode=3)  # the OCR layer
    path = tmp_path / "form.pdf"
    doc.save(path)
    blocks = _extract_blocks(fitz.open(path)[0], 0, ocr=True)
    assert sorted(b.text for b in blocks) == ["Kept on file", "Kind of Service"]
    for i, b in enumerate(blocks):
        b.id = f"b{i}"
    pa = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, 1, 0, 0), blocks=blocks)
    assert [pa.element(i).text for i in heuristic_reading_order(pa)] == ["Kind of Service", "Kept on file"]
    # Not OCR'd: one line, as before
    assert len(_extract_blocks(fitz.open(path)[0], 0)) == 1
