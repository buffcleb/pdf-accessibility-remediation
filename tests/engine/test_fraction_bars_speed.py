"""Finding fraction bars among a page's rules compared every horizontal rule with every other one (and with every
upright), so a page drawn as thousands of short rules, a scatter of dashes or a plotted curve, was quadratic there
and could hold a worker for its time limit. The rules are binned by row and column; the bars found are the same."""
import time

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.pipeline.analyzer import _fraction_bars

RULES = 12000


def _page_of_dashes(path):
    """RULES short rules, none within reach of another (so each is compared with all the rest), and three real
    fraction bars with a letter over and under each."""
    ops = []
    for n in range(RULES):
        row, col = divmod(n, 120)  # 100 rows of 120 dashes, 2 points apart along the row, 6.5 between rows
        x, y = 40 + col * 4.5, 720 - row * 6.5
        ops.append(f"{x:.1f} {y:.1f} m {x + 2.5:.1f} {y:.1f} l S")
    for x in (100, 300, 500):
        ops.append(f"BT /F1 10 Tf {x} 48 Td (a) Tj ET {x - 2} 44 m {x + 8} 44 l S BT /F1 10 Tf {x} 32 Td (b) Tj ET")
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                                        Resources=Dictionary(Font=Dictionary(F1=font)),
                                        Contents=pdf.make_stream(("0.5 w\n" + "\n".join(ops)).encode())))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)


def test_a_page_of_thousands_of_rules_is_searched_in_seconds(tmp_path):
    _page_of_dashes(tmp_path / "dashes.pdf")
    with fitz.open(tmp_path / "dashes.pdf") as doc:
        started = time.monotonic()
        bars = _fraction_bars(doc[0])
        took = time.monotonic() - started
    assert len(bars) == RULES + 3  # every isolated rule is a candidate bar; the three under the "a"s too
    assert took < 3.0, f"{RULES} rules took {took:.1f} s"
