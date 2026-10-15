"""Incomplete glyph lists in font descriptors (PDF/UA-1 7.21.4.2): removed without changing rendering,
on the standard route and in the Adobe route's improved copy."""
from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest

from app.pipeline.fonts import _CANDIDATES, fix_inserted_cid_fonts
from app.tagging.font_descriptors import remove_incomplete_glyph_lists
from app.validation.verapdf import RuleFailure

FONT = next((f for f in _CANDIDATES if Path(f).is_file()), None)
needs_font = pytest.mark.skipif(FONT is None, reason="no TrueType font to embed")


def _cid_doc(path: Path) -> Path:
    """A subset CID font whose /CIDSet claims only CID 0 (what some subsetting tools write)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontfile=FONT)
    page.insert_text((72, 100), "Hello world 123", fontname="emb", fontsize=14)
    page.insert_text((72, 140), "Standard font text", fontname="helv", fontsize=12)
    doc.subset_fonts()
    doc.save(path)
    fix_inserted_cid_fonts(str(path), fitz.Font(fontfile=FONT).name)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for o in pdf.objects:
            if isinstance(o, pikepdf.Dictionary) and o.get("/Subtype") == pikepdf.Name.CIDFontType2:
                o.FontDescriptor.CIDSet = pdf.make_stream(b"\x80")
        pdf.save(path)
    return path


def _font_name(path: Path) -> str:
    with pikepdf.open(path) as pdf:
        o = next(o for o in pdf.objects if isinstance(o, pikepdf.Dictionary)
                 and o.get("/Subtype") == pikepdf.Name.CIDFontType2)
        return str(o.BaseFont)[1:].split("+", 1)[1]


def _has_cidset(path: Path) -> bool:
    with pikepdf.open(path) as pdf:
        return any("/CIDSet" in o.FontDescriptor for o in pdf.objects if isinstance(o, pikepdf.Dictionary)
                   and o.get("/Subtype") == pikepdf.Name.CIDFontType2)


def _same_render(a: Path, b: Path) -> bool:
    return fitz.open(a)[0].get_pixmap(dpi=72).samples == fitz.open(b)[0].get_pixmap(dpi=72).samples


@needs_font
def test_reported_cidset_is_removed_and_nothing_else_changes(tmp_path):
    src = _cid_doc(tmp_path / "cid.pdf")
    failure = RuleFailure("7.21.4.2", "2", "ISO 14289-1:2014", "CIDSet", 1, fonts=[_font_name(src)])
    dst = tmp_path / "fixed.pdf"
    fixed = remove_incomplete_glyph_lists(src, dst, [failure])
    assert len(fixed) == 1 and "CIDSet" in fixed[0]
    assert not _has_cidset(dst) and _same_render(src, dst)


@needs_font
def test_other_fonts_and_other_rules_are_left_alone(tmp_path):
    src = _cid_doc(tmp_path / "cid.pdf")
    dst = tmp_path / "fixed.pdf"
    other_font = RuleFailure("7.21.4.2", "2", "ISO 14289-1:2014", "CIDSet", 1, fonts=["SomeOtherFont"])
    other_rule = RuleFailure("7.21.3.2", "1", "ISO 14289-1:2014", "CIDToGIDMap", 1, fonts=[_font_name(src)])
    assert remove_incomplete_glyph_lists(src, dst, [other_font, other_rule]) == []
    assert not dst.exists() and _has_cidset(src)


@needs_font
def test_verapdf_passes_the_rule_after_the_fix(tmp_path, verapdf_available):
    if not verapdf_available:
        pytest.skip("veraPDF not installed")
    from app.pipeline.orchestrator import _fix_glyph_lists, _primary, _validate

    pdf = _cid_doc(tmp_path / "cid.pdf")
    before = pdf.with_name("before.pdf")
    before.write_bytes(pdf.read_bytes())
    vera = _validate(pdf, tmp_path, "t", "cid")
    assert "7.21.4.2" in _primary(vera).failed_clauses
    vera, fixed = _fix_glyph_lists(pdf, vera, tmp_path, "t", "cid")
    assert fixed and "7.21.4.2" not in _primary(vera).failed_clauses
    assert _same_render(before, pdf)


@needs_font
def test_adobe_improved_copy_fixes_fonts_without_ai(tmp_path, verapdf_available, monkeypatch):
    if not verapdf_available:
        pytest.skip("veraPDF not installed")
    import asyncio

    from app.config import get_settings
    from app.pipeline import adobe_job
    from app.pipeline.orchestrator import JobOptions
    from app.storage import JobStore
    from tests.engine.test_adobe import _client
    from app.adobe.fake import FakeBehavior, create_app

    s = get_settings()
    for k, v in {"adobe_enabled": True, "adobe_client_id": "x", "adobe_client_secret": "y",
                 "adobe_base_url": "http://adobe.test", "adobe_poll_seconds": 0.0,
                 "adobe_max_pages_per_call": 100, "adobe_max_mb_per_call": 100}.items():
        monkeypatch.setattr(s, k, v)
    fake = create_app(tmp_path / "fake", FakeBehavior())
    monkeypatch.setattr(adobe_job, "AdobeClient", lambda: _client(fake))
    store = JobStore("00000000-0000-0000-0000-00000000cafe", root=tmp_path / "jobs")
    store.create()
    _cid_doc(store.input_pdf)
    result = asyncio.run(adobe_job.run_adobe(store, JobOptions(stem="cid", job_id=store.job_id)))
    reps = result["reports"]
    assert result["files"]["enhanced"] == "cid_adobe_improved.pdf"
    assert reps["improvements"]["glyph_lists"] and reps["improvements"]["alt_text"] == 0
    after = reps["comparison"]["after"]["verapdf"]["ua1"]["failed_clauses"]
    improved = reps["comparison"]["after_enhanced"]["verapdf"]["ua1"]["failed_clauses"]
    assert "7.21.4.2" in after and "7.21.4.2" not in improved  # Adobe's file as returned, the copy fixed
    assert _has_cidset(store.output_dir / "cid_adobe_tagged.pdf")
    assert "enhanced" not in (reps["tag_verification"] or {}) or \
        reps["tag_verification"]["enhanced"]["ai"].get("mode") in (None, "off")


@needs_font
def test_inserted_font_descriptor_is_named_like_the_font(tmp_path):
    """FontDescriptor /FontName must equal the CIDFont's /BaseFont (PDF spec; the Arlington checker tests it)."""
    src = _cid_doc(tmp_path / "cid.pdf")
    # What PyMuPDF writes with DejaVu Sans (the engine image's font): the descriptor named by the full name
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        for o in pdf.objects:
            if isinstance(o, pikepdf.Dictionary) and o.get("/Subtype") == pikepdf.Name.CIDFontType2:
                o.FontDescriptor.FontName = pikepdf.Name("/Full Font Name Book")
        pdf.save(src)
    assert fix_inserted_cid_fonts(str(src), fitz.Font(fontfile=FONT).name) >= 1
    with pikepdf.open(src) as pdf:
        fonts = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary)
                 and o.get("/Subtype") == pikepdf.Name.CIDFontType2]
        assert fonts and all(o.FontDescriptor.FontName == o.BaseFont for o in fonts)
