"""E2: the AI corrects OCR words Tesseract was unsure of, under checks that keep the text true to the scan."""
import asyncio
import io

import pymupdf as fitz
import pytest

from app.config import get_settings
from app.pipeline.analyzer import render_png
from app.pipeline.ocr import OcrLine, OcrWord, tesseract_cli
from app.pipeline.ocr_ai import _keep_punctuation, check_correction, check_transcription, marked_text, mode

needs_cli = pytest.mark.skipif(tesseract_cli() is None, reason="the Tesseract command line isn't installed")


def _line(*words, size=10.0, extent=200.0):
    ws = [OcrWord(t, c, (0, 0, 1, 1)) for t, c in words]
    return OcrLine(0, " ".join(t for t, _ in words), (0, 0, extent, size), (0, size), size, (1, 0), extent, ws)


def test_only_uncertain_words_change_and_must_look_alike():
    ok, _, changes, text = check_correction(_line(("Accessible", 95), ("Unmiversity.", 40)), "Accessible University")
    assert ok and text == "Accessible University." and changes == [("Unmiversity.", "University.")]  # punctuation kept
    ok, why, *_ = check_correction(_line(("the", 95), ("cat", 95)), "the dog")
    assert not ok and "sure of" in why
    ok, why, *_ = check_correction(_line(("x", 95), ("suddents", 50), ("y", 95)), "x many students y")
    assert not ok and "added" in why
    ok, why, *_ = check_correction(_line(("total", 95), ("12", 40)), "total seventy")
    assert not ok and "doesn't look like" in why


def test_spacing_fixes_and_specks():
    ok, _, changes, text = check_correction(_line(("Toprovide", 90), ("students", 95)), "To provide students")
    assert ok and text == "To provide students"  # re-spacing a confident word is fine
    ok, _, _, text = check_correction(_line(("the", 95), ("|", 30), ("end", 95)), "the end")
    assert ok and text == "the end"  # a speck read as punctuation may go
    ok, why, *_ = check_correction(_line(("the", 95), ("big", 30), ("end", 95)), "the end")
    assert not ok and "dropped" in why  # a real word may not


def test_no_other_writing_system():
    ok, why, *_ = check_correction(_line(("plan", 95), ("Dutline", 40)), "plan Оutline")  # Cyrillic О
    assert not ok and "writing system" in why


def test_transcription_must_fit_the_line():
    line = _line(("Atcessi", 20), ("wh", 10), ("pUnmiversity", 30), size=12, extent=160)
    assert check_transcription(line, "Accessible University")[0]
    assert not check_transcription(line, "[illegible]")[0]
    assert not check_transcription(line, "A" * 200)[0]
    assert not check_transcription(line, "one\ntwo")[0]


def test_which_lines_go_to_the_ai(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ocr_ai_min_conf", 80.0)
    monkeypatch.setattr(s, "ocr_ai_transcribe_below", 45.0)
    assert mode(_line(("All", 96), ("clear", 92))) is None
    assert mode(_line(("All", 96), ("cleer", 61))) == "correct"
    assert mode(_line(("Atcessi", 20), ("wh", 10), ("pUnmiversity", 30))) == "transcribe"
    assert mode(_line(("|", 20), ("___", 10))) is None  # table borders and leader lines: nothing to read
    assert marked_text(_line(("All", 96), ("cleer", 61))) == "All [[cleer]]"
    assert _keep_punctuation("“Hello,", "Hello") == "“Hello,"


def _scan(path, lines, dpi=150):
    """A scanned page (an image only) of the given text lines."""
    src = fitz.open()
    p = src.new_page(width=595, height=842)
    for i, t in enumerate(lines):
        p.insert_text((72, 100 + 22 * i), t, fontsize=13, fontname="tiro")
    pix = p.get_pixmap(dpi=dpi)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(path)
    return path


@needs_cli
def test_corrections_reach_the_text_layer_and_are_flagged(tmp_path, monkeypatch):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats
    from app.pipeline.ocr import ocr_lines, write_ocr_layer
    from app.pipeline.ocr_ai import correct_lines

    pdf = _scan(tmp_path / "scan.pdf", ["Remediation report for the quarter", "Every figure has alt text now"])
    monkeypatch.setattr(get_settings(), "ocr_ai_min_conf", 101.0)  # treat every word as uncertain

    async def fix(self, png, page, text, how):
        # Correct one word plausibly ("quarter" -> "quartet"), leave the rest
        return {"text": text.replace("[[", "").replace("]]", "").replace("quarter", "quartet"), "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "correct_ocr_line", fix)
    lines = ocr_lines(str(pdf), [0])
    assert lines and all(ln.words for ln in lines)
    stats = EnrichmentStats()

    async def go():
        async with MockProvider() as ai:
            await correct_lines(ai, str(pdf), lines, stats)

    asyncio.run(go())
    assert any(("quarter", "quartet") in o["changes"] for o in stats.ocr), stats.findings
    out = tmp_path / "ocr.pdf"
    write_ocr_layer(str(pdf), str(out), lines)
    text = fitz.open(out)[0].get_text()
    assert "quartet" in text and "Every figure" in text
    # the page itself is unchanged: only invisible text was added
    assert fitz.open(out)[0].get_pixmap(dpi=40).samples == fitz.open(pdf)[0].get_pixmap(dpi=40).samples


@needs_cli
def test_ocr_lines_split_table_rows_into_cells(tmp_path):
    from app.pipeline.ocr import ocr_lines

    pdf = tmp_path / "table.pdf"
    src = fitz.open()
    p = src.new_page(width=595, height=842)
    for x, t in ((72, "Week"), (250, "Topic"), (430, "Reading")):
        p.insert_text((x, 120), t, fontsize=13, fontname="tiro")
    p.insert_text((72, 200), "A sentence so the page has ordinary text as well.", fontsize=13, fontname="tiro")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=p.get_pixmap(dpi=150).tobytes("png"))
    doc.save(pdf)
    row = [ln for ln in ocr_lines(str(pdf), [0]) if ln.bbox[1] < 150]
    assert sorted(ln.text for ln in row) == ["Reading", "Topic", "Week"]


@needs_cli
def test_pipeline_reports_and_flags_ai_ocr(tmp_path, monkeypatch, samples):
    import shutil
    import uuid

    from app.ai.mock import MockProvider
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    monkeypatch.setattr(get_settings(), "ocr_ai_min_conf", 101.0)

    async def fix(self, png, page, text, how):
        words = text.replace("[[", "").replace("]]", "").split()
        if words and words[0].isalpha() and len(words[0]) > 3:
            words[0] = words[0][:-1] + ("e" if words[0][-1] != "e" else "a")
        return {"text": " ".join(words), "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "correct_ocr_line", fix)
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    shutil.copyfile(samples / "scanned.pdf", store.input_pdf)
    res = asyncio.run(run_remediation(store, JobOptions(stem="s", job_id=jid, provider="mock")))
    m = res["metrics"]
    assert m["ocr_pages"] == 1 and m["ocr_ai_words_corrected"] >= 1
    assert res["reports"]["ai_ocr"] and res["reports"]["ai_ocr"][0]["page"] == 1
    tv = res["reports"]["tag_verification"]["primary"]
    assert any(f["issue"] == "OCR text corrected by AI" for f in tv["findings"])
    assert "OCR: the AI corrected" in res["reports"]["remediation_summary"]


@needs_cli
def test_switched_off_per_job_keeps_tesseract(tmp_path, monkeypatch, samples):
    import shutil
    import uuid

    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    monkeypatch.setattr(get_settings(), "ocr_ai_min_conf", 101.0)
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    shutil.copyfile(samples / "scanned.pdf", store.input_pdf)
    res = asyncio.run(run_remediation(store, JobOptions(stem="s", job_id=jid, provider="mock", ocr_ai=False)))
    assert res["metrics"]["ocr_ai_words_corrected"] == 0 and not res["reports"]["ai_ocr"]


def test_pictures_read_as_letters_are_left_out(monkeypatch):
    """A bar chart's bars read as one tall, unsure "word" ("alll") would be written huge over the caption and
    become a heading; real large text (a title) is read confidently and stays."""
    from app.pipeline import ocr

    def row(key, text, conf, y0, y1, x0=50.0, x1=120.0):
        return {"key": key, "text": text, "conf": conf, "bbox": (x0, y0, x1, y1), "visual": (x0, y0, x1, y1)}

    rows = [row((1, 1, 1), "Annual", 96.0, 63, 85), row((1, 1, 1), "Report", 96.8, 63, 85, 130, 200),
            row((2, 1, 1), "alll", 37.0, 334, 483, 53, 251),
            row((3, 1, 1), "Figure", 96.4, 475, 484), row((3, 1, 1), "1:", 96.6, 475, 482, 125, 135),
            row((3, 1, 1), "Remediation", 96.5, 475, 482, 140, 200)]
    rows += [row((4, 1, i), "word", 95.0, 500, 509, 50 + 40 * i, 80 + 40 * i) for i in range(6)]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language: rows)
    page = fitz.open().new_page(width=595, height=842)
    lines = ocr._tsv_lines(page, "eng")
    texts = [ln.text for ln in lines]
    assert "alll" not in " ".join(texts)
    assert "Annual Report" in texts and "Figure 1: Remediation" in texts
    assert max(ln.size for ln in lines) < 25  # the title, not a picture
    # A tall word read confidently is kept (a big headline or a drop cap)
    rows[2]["conf"] = 92.0
    assert any(ln.text == "alll" for ln in ocr._tsv_lines(page, "eng"))


def _rotated_scan(path, lines, dpi=150):
    """A scan stored sideways in a landscape page, shown upright through /Rotate 90 (as scanners often save it)."""
    src = fitz.open()
    p = src.new_page(width=595, height=842)
    for i, t in enumerate(lines):
        p.insert_text((72, 100 + 22 * i), t, fontsize=13, fontname="tiro")
    pix = p.get_pixmap(dpi=dpi)
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_image(page.rect, stream=pix.tobytes("png"), rotate=90)
    page.set_rotation(90)
    doc.save(path)
    return path


def test_render_refuses_an_empty_clip():
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    with pytest.raises(ValueError, match="nothing to render"):
        render_png(doc, 0, clip=(300, 300, 400, 320))


def test_a_line_that_cant_be_rendered_keeps_tesseract(tmp_path):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats
    from app.pipeline.ocr_ai import correct_lines

    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    doc.save(pdf)
    line = _line(("Hello", 40), ("world", 95))
    line.bbox = (900, 900, 1000, 910)  # off the page
    stats = EnrichmentStats()

    async def go():
        async with MockProvider() as ai:
            await correct_lines(ai, str(pdf), [line], stats)

    asyncio.run(go())
    assert line.text == "Hello world" and any("Tesseract's text kept" in f for f in stats.findings)


@needs_cli
def test_rotated_scan_crops_the_line_upright(tmp_path, monkeypatch):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats
    from app.pipeline.ocr import ocr_lines
    from app.pipeline.ocr_ai import correct_lines

    pdf = _rotated_scan(tmp_path / "rot.pdf", ["Remediation report for the quarter", "Every figure has alt text now"])
    monkeypatch.setattr(get_settings(), "ocr_ai_min_conf", 101.0)
    crops = []

    async def fix(self, png, page, text, how):
        crops.append(fitz.Pixmap(png))
        return {"text": text.replace("[[", "").replace("]]", "").replace("quarter", "quartet"), "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "correct_ocr_line", fix)
    lines = ocr_lines(str(pdf), [0])
    stats = EnrichmentStats()

    async def go():
        async with MockProvider() as ai:
            await correct_lines(ai, str(pdf), lines, stats)

    asyncio.run(go())
    assert crops and not any("failed" in f for f in stats.findings), stats.findings
    assert any(("quarter", "quartet") in o["changes"] for o in stats.ocr)
    for pix in crops:  # each crop is a line lying flat, with ink in it
        assert pix.width > 3 * pix.height
        gray = fitz.Pixmap(fitz.csGRAY, pix)
        assert min(gray.samples) < 100


@needs_cli
def test_pipeline_completes_on_a_rotated_scan_with_ai_ocr(tmp_path, monkeypatch):
    import uuid

    from app.ai.mock import MockProvider
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    monkeypatch.setattr(get_settings(), "ocr_ai_min_conf", 101.0)

    async def fix(self, png, page, text, how):
        return {"text": text.replace("[[", "").replace("]]", "").replace("quarter", "quartet"), "confidence": 0.9}

    monkeypatch.setattr(MockProvider, "correct_ocr_line", fix)
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    _rotated_scan(store.input_pdf, ["Remediation report for the quarter", "Every figure has alt text now"])
    res = asyncio.run(run_remediation(store, JobOptions(stem="r", job_id=jid, provider="mock")))
    assert res["metrics"]["ocr_pages"] == 1 and res["metrics"]["ocr_ai_words_corrected"] >= 1


def test_render_pads_a_sliver():
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    assert fitz.Pixmap(render_png(doc, 0, dpi=150, clip=(72.0, 290.3, 540.0, 290.5))).height >= 1  # a 0.2 pt rule


def test_existing_figures_that_are_rules_or_fail_never_fail_the_job(tmp_path):
    """Word tags horizontal rules as figures: one 0.2 pt tall used to fail the whole job."""
    from app.pipeline.enrichment import EnrichmentStats
    from app.pipeline.orchestrator import _describe_regions

    pdf = tmp_path / "p.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(pdf)

    class AI:
        calls = 0

        async def describe_image(self, png, page, context):
            AI.calls += 1
            raise RuntimeError("the model fell over")

    stats = EnrichmentStats()
    out = asyncio.run(_describe_regions(AI(), pdf, [(0, (72.0, 290.3, 540.0, 290.5)), (0, (100, 100, 300, 250))], stats))
    assert out == [(0, (72.0, 290.3, 540.0, 290.5), None, True)]  # the rule: an artifact, without asking the AI
    assert AI.calls == 1 and any("failed" in f for f in stats.findings)  # the real figure: left for review
