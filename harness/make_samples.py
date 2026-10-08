"""Generate untagged (and one partially tagged) sample PDFs for the test harness.

    python harness/make_samples.py samples/
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.pipeline.fonts import _CANDIDATES, fix_inserted_cid_fonts  # noqa: E402

FONT_FILE = next((f for f in _CANDIDATES if Path(f).is_file()), None)


def _fonts(page: fitz.Page, embed: bool) -> tuple[str, str]:
    """(regular, bold) font names; embedded TrueType when available and requested, else base-14."""
    if embed and FONT_FILE:
        page.insert_font(fontname="emb", fontfile=FONT_FILE)
        return "emb", "emb"
    return "helv", "hebo"


def _save(doc: fitz.Document, path: Path) -> None:
    doc.save(path)
    if FONT_FILE:
        fix_inserted_cid_fonts(str(path), fitz.Font(fontfile=FONT_FILE).name)


LOREM = (
    "Accessible documents let people using screen readers, magnifiers and other assistive technology "
    "read content in the intended order. Structure tags describe headings, paragraphs, lists, tables and "
    "figures so that software can present them meaningfully."
)


def _image_pixmap(w: int = 160, h: int = 100) -> fitz.Pixmap:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    pix.clear_with(220)
    for x in range(w):  # simple diagonal bar chart-ish pattern
        for y in range(h - (x * h // w), h):
            if (x // 20) % 2 == 0:
                pix.set_pixel(x, y, (40, 90, 160))
    return pix


def _footer(page: fitz.Page, n: int, total: int, font: str) -> None:
    page.insert_text((page.rect.width / 2 - 30, page.rect.height - 30), f"Page {n} of {total}", fontsize=9, fontname=font)
    page.insert_text((50, 30), "ACME Annual Report", fontsize=9, fontname=font)


def simple_report(path: Path) -> None:
    doc = fitz.open()
    for n in range(2):
        page = doc.new_page(width=612, height=792)
        reg, bold = _fonts(page, embed=True)
        y = 80
        if n == 0:
            page.insert_text((50, y), "Annual Accessibility Report", fontsize=24, fontname=bold)
            y += 40
        page.insert_text((50, y), f"Section {n + 1}: Overview", fontsize=16, fontname=bold)
        y += 24
        rect = fitz.Rect(50, y, 562, y + 90)
        page.insert_textbox(rect, LOREM, fontsize=11, fontname=reg)
        y += 100
        for i, item in enumerate(["Headings and paragraphs", "Lists and tables", "Figures with alt text"]):
            page.insert_text((60, y + i * 18), f"• {item}", fontsize=11, fontname=reg)
        y += 70
        page.insert_image(fitz.Rect(50, y, 290, y + 150), pixmap=_image_pixmap())
        page.insert_text((50, y + 168), "Figure 1: Remediation progress by quarter", fontsize=9, fontname=reg)
        y += 190
        page.insert_textbox(fitz.Rect(50, y, 562, y + 80), LOREM, fontsize=11, fontname=reg)
        if n == 0:
            r = fitz.Rect(50, y + 90, 250, y + 104)
            page.insert_text((50, y + 101), "Visit the W3C WCAG overview", fontsize=11, color=(0, 0, 0.8), fontname=reg)
            page.insert_link({"kind": fitz.LINK_URI, "from": r, "uri": "https://www.w3.org/WAI/standards-guidelines/wcag/"})
        _footer(page, n + 1, 2, reg)
    doc.set_metadata({})
    _save(doc, path)


def two_column(path: Path) -> None:
    """Uses non-embedded base-14 fonts on purpose: full PDF/UA needs a destructive fix (dual output)."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 70), "Two Column Article", fontsize=22, fontname="hebo")
    for col in range(2):
        x0 = 50 + col * 270
        for k in range(3):
            page.insert_textbox(fitz.Rect(x0, 100 + k * 200, x0 + 245, 290 + k * 200),
                                f"Column {col + 1}, paragraph {k + 1}. " + LOREM, fontsize=10)
    doc.save(path)


def table_doc(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    reg, bold = _fonts(page, embed=True)
    page.insert_text((50, 70), "Quarterly Results", fontsize=18, fontname=bold)
    rows = [["Quarter", "Documents", "Pass rate"], ["Q1", "120", "64%"], ["Q2", "180", "71%"], ["Q3", "240", "83%"]]
    x0, y0, cw, rh = 50, 100, 150, 24
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = fitz.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(cell, color=(0, 0, 0), width=0.8)
            page.insert_text((cell.x0 + 6, cell.y1 - 8), val, fontsize=11, fontname=bold if r == 0 else reg)
    page.insert_textbox(fitz.Rect(50, 220, 562, 300), "The table above summarizes results. " + LOREM, fontsize=11,
                        fontname=reg)
    _save(doc, path)


def partially_tagged(path: Path, tmp: Path) -> None:
    """Hand-built partial tags: one valid P, one Figure missing Alt, one dangling element,
    one unmapped custom role, and an H3 with no preceding H1/H2. The rest is untagged."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    reg, bold = _fonts(page, embed=True)
    page.insert_text((50, 80), "Partially Tagged Document", fontsize=20, fontname=bold)
    page.insert_textbox(fitz.Rect(50, 100, 562, 170), "First paragraph. " + LOREM, fontsize=11, fontname=reg)
    page.insert_image(fitz.Rect(50, 190, 250, 315), pixmap=_image_pixmap())
    page.insert_textbox(fitz.Rect(50, 340, 562, 410), "Second paragraph, untagged. " + LOREM, fontsize=11, fontname=reg)
    _save(doc, tmp)

    with pikepdf.open(tmp) as pdf:
        pg = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(pg.obj))
        out, bt_seen, mcid = [], 0, 0
        tagged_bt = {0: "/H3", 1: "/MyPara"}  # title as H3 (skip), first paragraph with custom role
        in_mark = False
        for ins in ops:
            op = str(ins.operator) if isinstance(ins, pikepdf.ContentStreamInstruction) else ""
            if op == "BT" and bt_seen in tagged_bt:
                out.append(pikepdf.ContentStreamInstruction([Name(tagged_bt[bt_seen]), Dictionary(MCID=mcid)], pikepdf.Operator("BDC")))
                mcid += 1
                in_mark = True
            if op == "Do":
                out.append(pikepdf.ContentStreamInstruction([Name("/Figure"), Dictionary(MCID=mcid)], pikepdf.Operator("BDC")))
                out.append(ins)
                out.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")))
                mcid += 1
                continue
            out.append(ins)
            if op == "ET":
                if in_mark:
                    out.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")))
                    in_mark = False
                bt_seen += 1
        pg.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))

        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        docel = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
        h3 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H3, P=docel, Pg=pg.obj, K=0))
        para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/MyPara"), P=docel, Pg=pg.obj, K=1))
        fig = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=docel, Pg=pg.obj, K=2))
        ghost = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=docel, Pg=pg.obj, K=Array([42])))
        docel.K = Array([h3, para, fig, ghost])
        root.K = Array([docel])
        root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([h3, para, fig])])))
        root.ParentTreeNextKey = 1
        pg.obj.StructParents = 0
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        pdf.save(path)


def scanned(path: Path, source: Path) -> None:
    """Image-only page (no text layer), as produced by a scanner: exercises the OCR path."""
    src = fitz.open(source)
    pix = src[0].get_pixmap(dpi=150, alpha=False)
    doc = fitz.open()
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)


def main(out_dir: str = "samples") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    simple_report(out / "simple_report.pdf")
    two_column(out / "two_column.pdf")
    table_doc(out / "table_doc.pdf")
    scanned(out / "scanned.pdf", out / "simple_report.pdf")
    partially_tagged(out / "partially_tagged.pdf", out / ".partial_tmp.pdf")
    (out / ".partial_tmp.pdf").unlink(missing_ok=True)
    print(f"wrote samples to {out.resolve()}")


if __name__ == "__main__":
    main(*sys.argv[1:])
