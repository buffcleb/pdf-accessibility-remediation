"""Flattening a page of absurd size. The background raster, the look at the page's text and the OCR fallback
each set a dpi and trusted the page's size; a page a hundred thousand points across is gigabytes at RASTER_DPI.
They render at a lower resolution instead, as OCR and the pixel comparison already do."""
import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from app.tagging.destructive import flatten_pages

HUGE = 100_000  # points a side
MAX_RENDER_PX = 10_000


def _huge_page(path):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, HUGE, HUGE]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"BT /F1 12 Tf 72 700 Td (Flatten me) Tj ET")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)


@pytest.fixture
def renders(monkeypatch):
    """The long side, in pixels, each whole-page render asked for; the render itself is made tiny."""
    asked = []
    original = fitz.Page.get_pixmap

    def recording(self, *args, **kwargs):
        if kwargs.get("clip") is None:
            if "dpi" in kwargs:
                asked.append(HUGE * kwargs["dpi"] / 72)
                kwargs["dpi"] = 2
            elif "matrix" in kwargs:
                asked.append(HUGE * kwargs["matrix"].a)
                kwargs["matrix"] = fitz.Matrix(0.001, 0.001)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, "get_pixmap", recording)
    return asked


def test_flattening_renders_a_huge_page_within_the_cap(tmp_path, renders):
    _huge_page(tmp_path / "huge.pdf")
    flatten_pages(str(tmp_path / "huge.pdf"), str(tmp_path / "flat.pdf"), [0])
    assert len(renders) >= 2  # the look at the text, and the background raster
    assert all(px <= MAX_RENDER_PX + 1 for px in renders), renders
    with fitz.open(tmp_path / "flat.pdf") as doc:
        assert "Flatten me" in doc[0].get_text()  # the invisible text layer is still written
