"""Captions are tagged with what they describe: a figure's Caption beside it in a Sect (alt text hides a Figure's
children, so a Caption inside it wouldn't be read), a table's Caption as its first or last child."""
import asyncio
import shutil
import uuid

import pikepdf
import pymupdf as fitz

from app.pipeline.models import ImageRegion, PageAnalysis, TableRegion, TextBlock
from app.pipeline.structure import caption_pairs


def _page(*elements, order=None):
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792))
    for e in elements:
        (page.blocks if isinstance(e, TextBlock) else page.images if isinstance(e, ImageRegion) else page.tables).append(e)
    page.reading_order = order or [e.id for e in elements]
    return page


def _cap(id, bbox, text):
    return TextBlock(id=id, page=0, bbox=bbox, text=text, font_size=9, role="Caption")


def _fig(id, bbox, **kw):
    return ImageRegion(id=id, page=0, bbox=bbox, xref=0, **kw)


def _table(id, bbox):
    return TableRegion(id=id, page=0, bbox=bbox, n_rows=2, n_cols=2)


def test_a_figure_pairs_with_the_caption_below_it():
    fig, cap = _fig("i1", (50, 100, 290, 250)), _cap("b1", (50, 258, 260, 268), "Figure 1: Progress by quarter")
    assert caption_pairs(_page(fig, cap)) == {"b1": "i1"}


def test_a_table_pairs_with_the_caption_above_it():
    cap, table = _cap("b1", (50, 80, 300, 90), "Table 2. Results"), _table("t1", (50, 96, 500, 200))
    assert caption_pairs(_page(cap, table)) == {"b1": "t1"}


def test_kinds_must_match():
    fig, cap = _fig("i1", (50, 100, 290, 250)), _cap("b1", (50, 258, 260, 268), "Table 1: not this figure's")
    assert caption_pairs(_page(fig, cap)) == {}
    cap2, table = _cap("b2", (50, 80, 300, 90), "Figure 3: not a table's"), _table("t1", (50, 96, 500, 200))
    assert caption_pairs(_page(cap2, table)) == {}
    formula = _fig("i2", (50, 100, 290, 130), kind="formula")
    assert caption_pairs(_page(formula, _cap("b3", (50, 136, 260, 146), "Figure 4: x"))) == {}


def test_a_caption_far_away_or_not_next_in_reading_order_stays_a_paragraph():
    fig = _fig("i1", (50, 100, 290, 250))
    assert caption_pairs(_page(fig, _cap("b1", (50, 400, 260, 410), "Figure 1: too far"))) == {}
    between = TextBlock(id="b9", page=0, bbox=(320, 120, 560, 200), text="Body text", font_size=11)
    cap = _cap("b1", (50, 258, 260, 268), "Figure 1: Progress")
    assert caption_pairs(_page(fig, between, cap)) == {}
    assert caption_pairs(_page(fig, _cap("b2", (330, 258, 560, 268), "Figure 1: other column"))) == {}


def test_two_figures_two_captions():
    f1, c1 = _fig("i1", (50, 100, 290, 250)), _cap("b1", (50, 258, 260, 268), "Figure 1: First")
    f2, c2 = _fig("i2", (50, 300, 290, 450)), _cap("b2", (50, 458, 260, 468), "Figure 2: Second")
    assert caption_pairs(_page(f1, c1, f2, c2)) == {"b1": "i1", "b2": "i2"}


def _roles(path):
    """(role, [child roles]) for every element, in order."""
    out = []
    with pikepdf.open(path) as pdf:
        def walk(n):
            k = n.get("/K")
            for c in (list(k) if isinstance(k, pikepdf.Array) else [k]):
                if isinstance(c, pikepdf.Dictionary) and c.get("/S") is not None:
                    kids = c.get("/K")
                    kid_roles = [str(x.S)[1:] for x in (list(kids) if isinstance(kids, pikepdf.Array) else [kids])
                                 if isinstance(x, pikepdf.Dictionary) and x.get("/S") is not None]
                    out.append((str(c.S)[1:], kid_roles))
                    walk(c)
        walk(pdf.Root.StructTreeRoot)
    return out


def _remediate(tmp_path, src):
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    shutil.copyfile(src, store.input_pdf)
    res = asyncio.run(run_remediation(store, JobOptions(stem="c", job_id=jid, provider="mock")))
    return res, next(store.dir.rglob("c_remediated.pdf"))


def _table_with_caption(path):
    from harness.make_samples import _fonts, _save

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    reg, bold = _fonts(page, embed=True)
    page.insert_text((50, 60), "Quarterly Results", fontsize=18, fontname=bold)
    page.insert_text((50, 92), "Table 1: Documents remediated per quarter", fontsize=9, fontname=reg)
    rows = [["Quarter", "Documents", "Pass rate"], ["Q1", "120", "64%"], ["Q2", "180", "71%"], ["Q3", "240", "83%"]]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = fitz.Rect(50 + c * 150, 100 + r * 24, 50 + (c + 1) * 150, 100 + (r + 1) * 24)
            page.draw_rect(cell, color=(0, 0, 0), width=0.8)
            page.insert_text((cell.x0 + 6, cell.y1 - 8), val, fontsize=11, fontname=bold if r == 0 else reg)
    page.insert_textbox(fitz.Rect(50, 220, 562, 300), "The table above summarizes results.", fontsize=11, fontname=reg)
    _save(doc, path)
    return path


def test_figure_captions_are_tagged_beside_their_figures(tmp_path, samples, verapdf_available):
    res, out = _remediate(tmp_path, samples / "simple_report.pdf")
    roles = _roles(out)
    groups = [kids for role, kids in roles if "Caption" in kids]
    assert groups == [["Figure", "Caption"], ["Figure", "Caption"]], roles  # one per page, caption below
    assert not any(role == "Figure" and "Caption" in kids for role, kids in roles)  # never inside the Figure
    if verapdf_available:
        assert res["reports"]["comparison"]["after"]["compliant"] is True


def test_a_table_caption_is_the_tables_first_child(tmp_path, verapdf_available):
    res, out = _remediate(tmp_path, _table_with_caption(tmp_path / "table.pdf"))
    tables = [kids for role, kids in _roles(out) if role == "Table"]
    assert tables and tables[0][0] == "Caption" and set(tables[0][1:]) == {"TR"}, tables
    if verapdf_available:
        assert res["reports"]["comparison"]["after"]["compliant"] is True
