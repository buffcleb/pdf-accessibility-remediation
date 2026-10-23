"""#428: the checks of a save get faster without changing what they find. The contrast check is shared out over
processes like the page reading; the pixel comparison of an edit skips pages whose drawing didn't change; a tag's
short text is made from as many glyphs as it needs."""
import pikepdf
import pymupdf as fitz

from app.config import get_settings
from app.pipeline.orchestrator import _drawing, _visual_fidelity
from app.verification import contrast
from app.verification.semantic import Content, glyph_text


def _pages(path, n=6, faint=(2, 4)):
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        color = (0.8, 0.8, 0.8) if i in faint else (0, 0, 0)
        page.insert_text((72, 100), f"Page {i + 1} says something worth reading here.", fontsize=11, color=color)
    doc.save(path)
    return path


def test_the_contrast_check_finds_the_same_in_several_processes(tmp_path, monkeypatch):
    src = _pages(tmp_path / "c.pdf")
    s = get_settings()
    monkeypatch.setattr(s, "page_workers_min_pages", 2)
    monkeypatch.setattr(s, "page_workers", 1)
    one = contrast.check(src, 100)
    monkeypatch.setattr(s, "page_workers", 3)
    three = contrast.check(src, 100)
    assert [f.page for f in one] == [3, 5]
    assert [f.to_dict() for f in three] == [f.to_dict() for f in one]
    assert [f.page for f in contrast.check(src, 4)] == [3]  # the page limit holds


def test_an_edits_unchanged_pages_arent_drawn_to_compare(tmp_path, monkeypatch):
    src = _pages(tmp_path / "a.pdf", faint=())
    same = tmp_path / "same.pdf"
    with pikepdf.open(src) as pdf:  # only the tag tree side changes: a title, as the tag editor's own edits would
        pdf.docinfo["/Title"] = "Edited"
        pdf.save(same)
    assert _drawing(src) == _drawing(same)
    drawn = []
    real = fitz.Page.get_pixmap
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda self, *a, **k: drawn.append(self.number) or real(self, *a, **k))
    assert _visual_fidelity(src, same, edited=True)["identical"] is True
    assert drawn == []
    # A page whose drawing did change is drawn, and a real difference is found
    changed = tmp_path / "changed.pdf"
    doc = fitz.open(src)
    doc[3].insert_text((72, 300), "An extra line.", fontsize=11)
    doc.save(changed)
    result = _visual_fidelity(src, changed, edited=True)
    assert result["identical"] is False and result["differing_pages"] == [4]
    assert set(drawn) == {3}
    # Not an edit (a corrected file): every page is drawn
    drawn.clear()
    _visual_fidelity(src, same)
    assert sorted(set(drawn)) == list(range(6))


def test_short_text_is_the_start_of_the_whole_text():
    chars = []
    x = 0.0
    for n, word in enumerate(("Results", "of", "the", "survey", "were", "clear", "and", "early") * 40):
        top = 100.0 + 14 * (n // 9)
        x = 72.0 if n % 9 == 0 else x
        for ch in word:
            chars.append({"text": ch, "size": 11.0, "x0": x, "x1": x + 6, "top": top})
            x += 6
        x += 4  # a gap for the word space, drawn by moving the pen
    c = Content(chars=chars)
    full = c.text
    for limit in (1, 5, 80, 200, 5000):
        assert c.text_upto(limit) == full[:limit], limit
    assert glyph_text(chars, 10)[0] == full[:10]
