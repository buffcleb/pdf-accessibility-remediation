"""A scanned form with values typed onto it (#292): the page counts as scanned and is OCR'd, the typed values keep
their own text and aren't read again, and slides with a background photo aren't taken for scans."""
import pymupdf as fitz

from app.pipeline import ocr
from app.pipeline.orchestrator import prepare_analysis

FORM = ["CERTIFICATE OF EXEMPTION", "Name of the vendor ______________________",
        "This certifies that the purchases below are exempt from the tax.", "Date ________"]


def _scan(colorspace=fitz.csGRAY, dpi=200, words=FORM):
    """A page-sized picture of a printed page, as a scanner makes it."""
    paper = fitz.open()
    p = paper.new_page(width=612, height=792)
    for n, line in enumerate(words):
        p.insert_text((72, 120 + 30 * n), line, fontsize=12, fontname="helv")
    return p.get_pixmap(dpi=dpi, colorspace=colorspace)


def _page(tmp_path, *, typed=("Acme Supplies Ltd", "1/2/26"), pix=None, invisible=None, name="form.pdf"):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, pixmap=pix or _scan())
    for n, value in enumerate(typed):
        page.insert_text((200 + 150 * n, 146 + 60 * n), value, fontsize=11, fontname="tiro")
    if invisible:  # a scan someone already OCR'd: its whole text, invisible
        for n, line in enumerate(invisible):
            page.insert_text((72, 120 + 30 * n), line, fontsize=12, fontname="helv", render_mode=3)
    path = tmp_path / name
    doc.save(path)
    return path


def test_a_scan_with_values_typed_onto_it_counts_as_scanned(tmp_path):
    assert prepare_analysis(_page(tmp_path)).pages[0].is_scanned
    assert prepare_analysis(_page(tmp_path, typed=())).pages[0].is_scanned         # a plain scan, as before


def test_what_isnt_taken_for_a_scan(tmp_path):
    # A slide: a color photograph behind a few words
    photo = _page(tmp_path, pix=_scan(colorspace=fitz.csRGB), typed=("Quarterly results",), name="slide.pdf")
    assert not prepare_analysis(photo).pages[0].is_scanned
    # A gray picture at screen resolution is a picture, not a scan
    low = _page(tmp_path, pix=_scan(dpi=72), name="low.pdf")
    assert not prepare_analysis(low).pages[0].is_scanned
    # A scan that already has its whole text (OCR'd before): nothing to read again
    done = _page(tmp_path, typed=(), invisible=FORM * 6, name="done.pdf")
    assert not prepare_analysis(done).pages[0].is_scanned


def _row(key, text, x0, x1, y0=100, y1=112):
    return {"key": key, "text": text, "conf": 95.0, "bbox": (x0, y0, x1, y1), "visual": (x0, y0, x1, y1)}


def test_ocr_leaves_typed_values_alone_and_splits_the_line_there(tmp_path, monkeypatch):
    # One printed line, "Name of vendor", with a typed value OCR also read, then "Date" on the same line
    rows = [_row((1, 1, 1), "Name", 72, 100), _row((1, 1, 1), "of", 103, 113), _row((1, 1, 1), "vendor", 116, 150),
            _row((1, 1, 1), "Acme", 160, 190), _row((1, 1, 1), "Supplies", 193, 240),
            _row((1, 1, 1), "Date", 250, 275)]
    monkeypatch.setattr(ocr, "_tesseract_tsv", lambda page, language, dpi=300: rows)
    page = fitz.open().new_page(width=612, height=792)
    typed = [(158, 99, 242, 113)]          # where the typed value is
    lines = ocr._tsv_lines(page, "eng", existing=typed)
    assert [ln.text for ln in lines] == ["Name of vendor", "Date"]       # split where the typed value was
    assert lines[1].bbox[0] >= 249                                      # each part keeps its own place
    assert [ln.text for ln in ocr._tsv_lines(page, "eng")] == ["Name of vendor Acme Supplies Date"]  # nothing typed


def test_a_word_counts_as_typed_text_when_half_of_it_lies_on_it():
    assert ocr._covered((100, 100, 140, 110), [(95, 98, 125, 112)])     # 25 of 40 points across
    assert not ocr._covered((100, 100, 140, 110), [(130, 98, 160, 112)])  # 10 of 40
