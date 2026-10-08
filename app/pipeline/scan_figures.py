"""Figures inside scanned pages.

A scanned page is one picture of the paper. After OCR its text is carried by an invisible text layer and the scan
becomes a background artifact, so a chart or photo printed on the page would get no tag and no alt text. This
finds those pictures (ink that OCR didn't read as text, plus what it misread as letters: see ocr._drop_graphics)
and draws each region of the scan again on top of itself, clipped to the region. The same image is drawn with the
same matrix, so the page looks exactly the same; the clipped drawing is what the Figure tag marks (the content
marker reads rectangle clips), and the rest of the scan stays background.

Coordinates are PyMuPDF's unrotated page space, as OCR's are; rotated pages are left alone.
"""
from __future__ import annotations

import logging
from collections import deque

import fitz
import numpy as np
import pikepdf

from app.pipeline.models import BBox, ImageRegion, PageAnalysis, bbox_area, bbox_intersection

log = logging.getLogger(__name__)

CELL = 6.0            # points per grid cell
CELL_INK = 0.12       # share of a cell's pixels that must be ink for it to count
MIN_W, MIN_H = 48.0, 36.0
MIN_AREA = 4000.0     # square points: about 1.2 by 0.8 inches
MAX_PAGE_SHARE = 0.85  # bigger than this is the page's own background, not a picture
MAX_TEXT_SHARE = 0.35  # more of a region covered by OCR text than this: a text panel or shaded table, not a picture
TEXT_PAD = 1.5


def _ink_mask(page: fitz.Page) -> np.ndarray:
    """True where the scan is darker than its paper (one pixel per point)."""
    pix = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    paper = float(np.median(arr))
    return arr < max(0.0, paper - 28.0)


def _components(grid: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(grid, dtype=bool)
    rows, cols = grid.shape
    out = []
    for r0 in range(rows):
        for c0 in range(cols):
            if not grid[r0, c0] or seen[r0, c0]:
                continue
            comp, queue = [], deque([(r0, c0)])
            seen[r0, c0] = True
            while queue:
                r, c = queue.popleft()
                comp.append((r, c))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols and grid[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        queue.append((rr, cc))
            out.append(comp)
    return out


def _grow(grid: np.ndarray, axis: int, by: int = 2) -> np.ndarray:
    """Each true cell also marks its neighbors up to `by` cells away along one axis (no wrapping at the edges)."""
    out = grid.copy()
    n = grid.shape[axis]
    for d in range(1, by + 1):
        if d >= n:
            break
        lo = [slice(None)] * 2
        hi = [slice(None)] * 2
        lo[axis], hi[axis] = slice(0, n - d), slice(d, n)
        out[tuple(lo)] |= grid[tuple(hi)]
        out[tuple(hi)] |= grid[tuple(lo)]
    return out


def find_regions(page: fitz.Page, text_boxes: list, hints: list | None = None) -> list[BBox]:
    """Picture regions on a scanned page: ink outside the OCR text (text_boxes), joined across small gaps, plus the
    boxes OCR read as letters but that look like pictures (hints)."""
    if page.rotation:
        return []
    ink = _ink_mask(page)
    h, w = ink.shape
    text = np.zeros_like(ink)
    for b in text_boxes:
        x0, y0 = max(0, int(b[0] - TEXT_PAD)), max(0, int(b[1] - TEXT_PAD))
        x1, y1 = min(w, int(b[2] + TEXT_PAD) + 1), min(h, int(b[3] + TEXT_PAD) + 1)
        text[y0:y1, x0:x1] = True
    pic = ink & ~text

    rows, cols = int(np.ceil(h / CELL)), int(np.ceil(w / CELL))
    padded = np.zeros((rows * int(CELL), cols * int(CELL)), dtype=bool)
    padded[:h, :w] = pic
    share = padded.reshape(rows, int(CELL), cols, int(CELL)).mean(axis=(1, 3))
    grid = share > CELL_INK
    for b in hints or []:
        grid[max(0, int(b[1] // CELL)):int(np.ceil(b[3] / CELL)), max(0, int(b[0] // CELL)):int(np.ceil(b[2] / CELL))] = True
    # Join the parts of one picture (a chart's bars, a diagram's boxes) across gaps of up to two cells
    joined = _grow(_grow(grid, 0), 1)

    page_area = float(w * h)
    regions: list[BBox] = []
    for comp in _components(joined):
        rs = [r for r, _ in comp]
        cs = [c for _, c in comp]
        # The ink actually inside the component's cells, not the joining margin
        sub = pic[min(rs) * int(CELL):(max(rs) + 1) * int(CELL), min(cs) * int(CELL):(max(cs) + 1) * int(CELL)]
        ys, xs = np.nonzero(sub)
        if len(xs) == 0:
            continue
        x0, y0 = min(cs) * CELL + xs.min(), min(rs) * CELL + ys.min()
        x1, y1 = min(cs) * CELL + xs.max() + 1, min(rs) * CELL + ys.max() + 1
        box = (max(0.0, x0 - 2), max(0.0, y0 - 2), min(float(w), x1 + 2), min(float(h), y1 + 2))
        bw, bh = box[2] - box[0], box[3] - box[1]
        if bw < MIN_W or bh < MIN_H or bw * bh < MIN_AREA or bw * bh > MAX_PAGE_SHARE * page_area:
            continue
        covered = sum(bbox_intersection(box, t) for t in text_boxes)
        if covered > MAX_TEXT_SHARE * bw * bh:
            continue
        regions.append(box)
    # Overlapping finds are one picture
    merged: list[BBox] = []
    for r in sorted(regions, key=bbox_area, reverse=True):
        for i, m in enumerate(merged):
            if bbox_intersection(r, m) > 0:
                merged[i] = (min(r[0], m[0]), min(r[1], m[1]), max(r[2], m[2]), max(r[3], m[3]))
                break
        else:
            merged.append(r)
    return sorted(merged, key=lambda b: (b[1], b[0]))


def _scan_xref(page: fitz.Page) -> int | None:
    """The page's scan: the image covering most of it."""
    area = abs(page.rect)
    best = None
    for info in page.get_image_info(xrefs=True):
        if info.get("xref") and abs(fitz.Rect(info["bbox"])) > 0.8 * area:
            if best is None or abs(fitz.Rect(info["bbox"])) > abs(fitz.Rect(best["bbox"])):
                best = info
    return best["xref"] if best else None


def _draw_matrix(page: pikepdf.Page, name: pikepdf.Name) -> tuple | None:
    """The matrix the page's content draws the named image with (PDF user space), read from the content stream."""
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack = []
    for ins in pikepdf.parse_content_stream(page.obj):
        op = str(ins.operator)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else ctm
        elif op == "cm" and len(ins.operands) == 6:
            a, b, c, d, e, f = (float(v) for v in ins.operands)
            A, B, C, D, E, F = ctm
            ctm = (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)
        elif op == "Do" and ins.operands and ins.operands[0] == name:
            return ctm
    return None


def add_redraws(src: str, dst: str, regions: dict[int, list[BBox]]) -> dict[int, list[BBox]]:
    """Draw each region of its page's scan again, clipped to the region, at the end of the page's content (so it
    lies over the same pixels). Returns the regions actually drawn, by page."""
    doc = fitz.open(src)
    plans: dict[int, tuple[int, fitz.Matrix, list[BBox]]] = {}
    try:
        for pno, boxes in regions.items():
            page = doc[pno]
            xref = _scan_xref(page)
            if xref is None or not boxes:
                continue
            plans[pno] = (xref, ~page.transformation_matrix, boxes)
    finally:
        doc.close()

    drawn: dict[int, list[BBox]] = {}
    with pikepdf.open(src) as pdf:
        for pno, (xref, to_pdf, boxes) in plans.items():
            page = pdf.pages[pno]
            xobjects = page.obj.get("/Resources", {}).get("/XObject", {})
            name = next((pikepdf.Name(k) for k, v in xobjects.items() if v.objgen[0] == xref), None)
            m = _draw_matrix(page, name) if name is not None else None
            if m is None:
                log.info("scanned page %d: its scan is drawn indirectly, so its figures aren't separated", pno + 1)
                continue
            ops = []
            for b in boxes:
                r = (fitz.Rect(b) * to_pdf).normalize()
                ops.append(f"q {r.x0:.4f} {r.y0:.4f} {r.width:.4f} {r.height:.4f} re W n "
                           + " ".join(f"{v:.6f}" for v in m) + f" cm {name} Do Q")
            # Isolate the existing content so its graphics state can't leak into the redraw
            page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
            page.contents_add(pdf.make_stream(("Q\n" + "\n".join(ops) + "\n").encode()))
            drawn[pno] = list(boxes)
        pdf.save(dst)
    return drawn


def add_to_page(page: PageAnalysis, boxes: list[BBox]) -> int:
    """Each region becomes a figure on the page (described and tagged like any other). Text OCR read inside it
    (a chart's labels) belongs to the figure, as for vector charts; a caption below it stays text."""
    for i, b in enumerate(boxes):
        # No object number: each region is its own picture (images sharing one share a description)
        page.images.append(ImageRegion(id=f"p{page.number}s{i}", page=page.number, bbox=b, xref=0))
    page.blocks = [blk for blk in page.blocks if blk.role == "Caption" or not any(
        bbox_intersection(blk.bbox, b) >= 0.8 * (bbox_area(blk.bbox) or 1.0) for b in boxes)]
    return len(boxes)


def separate(src: str, dst: str, pages: list[int], text_boxes: dict[int, list],
             hints: dict[int, list]) -> dict[int, list[BBox]]:
    """Find the figures on these scanned pages and write dst with each one drawn on its own (see add_redraws).
    Returns the figures by page ({} and no dst when there are none)."""
    doc = fitz.open(src)
    try:
        found = {p: r for p in pages if (r := find_regions(doc[p], text_boxes.get(p, []), hints.get(p)))}
    finally:
        doc.close()
    return add_redraws(src, dst, found) if found else {}

