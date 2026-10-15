"""A page of absurd size. The renders that aren't for the AI (the pixel comparison, OCR) set a dpi and trusted the
page's size; a page a hundred thousand points across (PyMuPDF accepts it) is gigabytes at 50 dpi and far more at
300. They are rendered at a lower resolution instead."""
import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import _visual_fidelity

HUGE = 100_000  # points a side
MAX_RENDER_PX = 10_000  # the long side any whole-page render may reach, in pixels


def _huge_page(path, side=HUGE):
    pdf = pikepdf.new()
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, side, side]),
                                        Contents=pdf.make_stream(b"0 0 50 50 re f")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)


@pytest.fixture
def dpi_requests(monkeypatch):
    """The dpi each whole-page render asks for; the render itself is made tiny so the test never allocates."""
    asked = []
    original = fitz.Page.get_pixmap

    def recording(self, *args, **kwargs):
        if "dpi" in kwargs:
            asked.append(kwargs["dpi"])
            kwargs["dpi"] = 2
        return original(self, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, "get_pixmap", recording)
    return asked


def test_the_cap_lowers_the_dpi_of_a_huge_page_only(tmp_path):
    from app.pipeline.analyzer import capped_dpi

    _huge_page(tmp_path / "huge.pdf")
    _huge_page(tmp_path / "letter.pdf", side=792)
    with fitz.open(tmp_path / "huge.pdf") as doc:
        dpi = capped_dpi(doc[0], 300)
        assert 1 <= dpi and doc[0].rect.width * dpi / 72 <= MAX_RENDER_PX
    with fitz.open(tmp_path / "letter.pdf") as doc:
        assert capped_dpi(doc[0], 300) == 300 and capped_dpi(doc[0], 50) == 50


def test_the_pixel_comparison_renders_a_huge_page_within_the_cap(tmp_path, dpi_requests):
    _huge_page(tmp_path / "huge.pdf")
    result = _visual_fidelity(tmp_path / "huge.pdf", tmp_path / "huge.pdf")
    assert result["identical"] and dpi_requests
    assert all(HUGE * d / 72 <= MAX_RENDER_PX for d in dpi_requests), dpi_requests


def test_ocr_renders_a_huge_page_within_the_cap(tmp_path, dpi_requests):
    from app.pipeline import ocr

    if ocr.tesseract_cli() is None:
        pytest.skip("the Tesseract command line isn't installed")
    _huge_page(tmp_path / "huge.pdf")
    with fitz.open(tmp_path / "huge.pdf") as doc:
        ocr._tesseract_tsv(doc[0], "eng")
    assert dpi_requests and all(HUGE * d / 72 <= MAX_RENDER_PX for d in dpi_requests), dpi_requests
