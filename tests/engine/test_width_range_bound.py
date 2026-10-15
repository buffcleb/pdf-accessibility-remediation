"""A CID font's /W array can say "c_first c_last w" for a range of glyphs. A CID is two bytes, so a range past
65535 is the file's error; the two places that expand such ranges used to build every entry of whatever range
the file claimed, and a file claiming millions took the worker's memory."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.content_marker import _font_widths
from app.tagging.font_metrics import _parse_w

ABSURD = 5_000_000  # enough to show (and time) the problem, small enough for a test machine
MAX_CID = 65535


def _cid_font(pdf, w):
    desc = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name.Wide,
                                        CIDSystemInfo=Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
                                        DW=1000, W=Array(w)))
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type0, BaseFont=Name.Wide,
                                        Encoding=Name("/Identity-H"), DescendantFonts=Array([desc])))


def test_the_marker_bounds_a_width_range_to_two_bytes():
    pdf = pikepdf.new()
    font = _cid_font(pdf, [0, ABSURD, 500, 70000, Array([600])])
    widths = _font_widths(font, {})
    assert widths is not None and len(widths) == MAX_CID + 2  # the bounded range, plus the explicit entry
    assert widths.get(65535) == 500 and widths.get(70000) == 600  # the explicit form is untouched
    assert widths.get(65536) == 1000  # beyond the bound: the default width


def test_the_width_fixer_bounds_a_width_range_to_two_bytes():
    pdf = pikepdf.new()
    font = _cid_font(pdf, [10, ABSURD, 500])
    widths = _parse_w(font.DescendantFonts[0].W)
    assert len(widths) == MAX_CID + 1 - 10 and 10 in widths and 65535 in widths and 65536 not in widths
