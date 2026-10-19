"""#124: a long document's layout analysis shared out over several processes gives exactly what one process gives."""

import pymupdf as fitz
import pytest

from app.config import get_settings
from app.pipeline import page_pool
from app.pipeline.analyzer import analyze_document


def _long_pdf(path, pages=12):
    """Pages of different kinds: headings and paragraphs, a ruled table, a picture, a drawn chart, a rotated page."""
    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page()
        page.insert_text((72, 80), f"Chapter {n + 1}", fontsize=20)
        for line in range(8):
            page.insert_text((72, 120 + 16 * line), f"Line {line} of page {n + 1}: the results held for x^2 + 1.",
                             fontsize=11)
        if n % 3 == 0:
            for r in range(4):
                for c in range(3):
                    rect = fitz.Rect(72 + 120 * c, 300 + 22 * r, 192 + 120 * c, 322 + 22 * r)
                    page.draw_rect(rect, color=(0, 0, 0), width=0.7)
                    page.insert_text((rect.x0 + 4, rect.y1 - 6), f"r{r}c{c}", fontsize=9)
        if n % 4 == 1:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 40), False)
            pix.set_rect(pix.irect, (200, 30, 30))
            page.insert_image(fitz.Rect(72, 420, 252, 540), pixmap=pix)
        if n % 4 == 2:
            for i in range(30):
                page.draw_line((300 + 5 * i, 600), (300 + 5 * i, 600 - 3 * (i % 7) - 10), width=2)
        if n == pages - 1:
            page.set_rotation(90)
    doc.save(path)
    return path


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "page_workers_min_pages", 4)
    return s


def test_shared_out_over_processes_the_analysis_is_the_same(tmp_path, settings, monkeypatch):
    src = _long_pdf(tmp_path / "long.pdf")
    monkeypatch.setattr(settings, "page_workers", 1)
    one = analyze_document(src)
    monkeypatch.setattr(settings, "page_workers", 3)
    calls = []
    real = page_pool.run
    monkeypatch.setattr(page_pool, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    three = analyze_document(src)
    assert calls and calls[0][3] == 3  # it did go to three processes
    assert three == one
    assert repr(three) == repr(one)  # every field, every float to the last digit
    assert any(p.tables for p in one.pages) and any(p.images for p in one.pages) and one.pages[-1].rotation == 90


def test_short_documents_and_a_single_worker_stay_in_one_process(settings, monkeypatch):
    monkeypatch.setattr(settings, "page_workers", 3)
    assert page_pool.workers_for(3) == 1 and page_pool.workers_for(4) == 3 and page_pool.workers_for(200) == 3
    monkeypatch.setattr(settings, "page_workers", 8)
    assert page_pool.workers_for(5) == 5  # never more processes than pages
    monkeypatch.setattr(settings, "page_workers", 1)
    assert page_pool.workers_for(500) == 1


def test_a_worker_that_fails_means_the_work_is_done_in_one_process(tmp_path, settings, monkeypatch):
    src = _long_pdf(tmp_path / "long.pdf", pages=6)
    assert page_pool.run("app.pipeline.analyzer:no_such_function", src, 6, 2) is None
    monkeypatch.setattr(settings, "page_workers", 2)
    monkeypatch.setattr(page_pool, "run", lambda *a, **k: None)
    assert len(analyze_document(src).pages) == 6


def _checked(path, settings, monkeypatch, workers):
    import pikepdf

    from app.verification.integrity import check_integrity
    from app.verification.semantic import SemanticChecker
    from app.verification.tagmap import artifact_sections

    monkeypatch.setattr(settings, "page_workers", workers)
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        checker = SemanticChecker(path, pdf, index)
        findings = checker.run()
        collected = repr((checker.own, checker.by_mcid, dict(checker.artifact), checker.tables, checker.page_size,
                          checker.skipped))
        return collected, repr(findings), artifact_sections(path)


def test_shared_out_over_processes_the_checks_read_the_same(tmp_path, settings, monkeypatch):
    from app.tagging.tagger import tag_document

    src = _long_pdf(tmp_path / "long.pdf")
    tagged = tmp_path / "tagged.pdf"
    monkeypatch.setattr(settings, "page_workers", 1)
    tag_document(src, tagged, analyze_document(src), title="Long", language="en-US")
    calls = []
    real = page_pool.run
    monkeypatch.setattr(page_pool, "run", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    one = _checked(tagged, settings, monkeypatch, 1)
    three = _checked(tagged, settings, monkeypatch, 3)
    assert calls == ["app.verification.semantic:collect_pages", "app.verification.tagmap:artifact_pages"]
    assert three == one
    assert "Content(chars=[{" in one[0] and one[2]  # there was something to compare


def test_the_page_limit_holds_with_processes_too(tmp_path, settings, monkeypatch):
    import pikepdf

    from app.verification.integrity import check_integrity
    from app.verification.semantic import SemanticChecker
    from app.tagging.tagger import tag_document

    src = _long_pdf(tmp_path / "long.pdf")
    tagged = tmp_path / "tagged.pdf"
    tag_document(src, tagged, analyze_document(src), title="Long", language="en-US")
    monkeypatch.setattr(settings, "page_workers", 3)
    with pikepdf.open(tagged) as pdf:
        _, index = check_integrity(pdf)
        checker = SemanticChecker(tagged, pdf, index, max_pages=7)
        checker._collect()
    assert sorted(checker.page_size) == list(range(7))
    assert checker.skipped == ["semantic checks limited to the first 7 pages"]
