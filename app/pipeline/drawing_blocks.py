"""The drawings in an area of a page, found by where its ink is: for asking the AI which of them make up each figure.

A vision model names what's on a page well and places boxes badly (on an assembly poster, a components key drawn
60 points too low, two motors swallowed into the board beside them). Given the drawings found here, lettered on
the picture, it chooses letters instead, and each figure's box is the drawings it chose.

Ink is what's darker than the paper (the scanned-page figures' test, scan_figures). Thin lines (a diagram's dashed
wiring, arrows to labels) join drawings that are apart, so lines one or two points wide are taken out first; then
ink within a few points is one drawing. Blocks that are mostly text already read (labels, OCR'd words) aren't
drawings: the text is given to the AI as its own numbered items."""
from __future__ import annotations

import numpy as np
import pymupdf as fitz

from app.pipeline.models import BBox
from app.pipeline.scan_figures import _components, _ink_mask

CELL = 2           # points per cell of the grid the drawings are found on
THIN = 3           # lines thinner than this many points are taken out (opening)
JOIN = 7           # ink this many points apart is one drawing
MIN_AREA = 400.0   # square points: smaller is a mark, not a drawing
MAX_TEXT = 0.5     # more of a block than this covered by text items: it's text
MAX_BLOCKS = 60    # lettered on one picture; past this, the largest


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(a)
    ys, yd = (slice(dy, None), slice(0, a.shape[0] - dy)) if dy >= 0 else (slice(0, dy), slice(-dy, None))
    xs, xd = (slice(dx, None), slice(0, a.shape[1] - dx)) if dx >= 0 else (slice(0, dx), slice(-dx, None))
    out[yd, xd] = a[ys, xs]
    return out


def _erode(a: np.ndarray, k: int) -> np.ndarray:
    out = a.copy()
    for dy in range(-(k // 2), k // 2 + 1):
        for dx in range(-(k // 2), k // 2 + 1):
            out &= _shift(a, dy, dx)
    return out


def _dilate(a: np.ndarray, k: int) -> np.ndarray:
    out = a.copy()
    for dy in range(-(k // 2), k // 2 + 1):
        for dx in range(-(k // 2), k // 2 + 1):
            out |= _shift(a, dy, dx)
    return out


def _covered(b: BBox, boxes: list) -> float:
    area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
    return sum(max(0.0, min(b[2], t[2]) - max(b[0], t[0])) * max(0.0, min(b[3], t[3]) - max(b[1], t[1]))
               for t in boxes) / area


def find(page: fitz.Page, area: BBox, text_boxes: list) -> list[BBox]:
    """The drawings mostly inside `area` (page units, as the tag map's), in reading order (top to bottom, then left
    to right). text_boxes: the boxes of the text items given with them."""
    if page.rotation:
        return []
    ink = _ink_mask(page)  # one pixel a point, the page's unrotated space
    h, w = ink.shape
    solid = _dilate(_erode(ink, THIN), THIN)
    joined = _dilate(solid, JOIN)
    rows, cols = h // CELL, w // CELL
    grid = joined[:rows * CELL, :cols * CELL].reshape(rows, CELL, cols, CELL).any(axis=(1, 3))
    page_area = float(w * h)
    blocks: list[BBox] = []
    for comp in _components(grid):
        rs = [r for r, _ in comp]
        cs = [c for _, c in comp]
        b = (float(min(cs) * CELL), float(min(rs) * CELL), float((max(cs) + 1) * CELL), float((max(rs) + 1) * CELL))
        size = (b[2] - b[0]) * (b[3] - b[1])
        if size < MIN_AREA or size > 0.85 * page_area or _covered(b, text_boxes) > MAX_TEXT:
            continue
        inside = max(0.0, min(area[2], b[2]) - max(area[0], b[0])) * max(0.0, min(area[3], b[3]) - max(area[1], b[1]))
        if inside >= 0.5 * size:
            blocks.append(b)
    if len(blocks) > MAX_BLOCKS:
        blocks = sorted(blocks, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)[:MAX_BLOCKS]
    return sorted(blocks, key=lambda b: (round(b[1] / 20), b[0]))


def letters(n: int) -> list[str]:
    """A, B, ... Z, AA, AB, ...: the drawings' names on the picture and in the answer."""
    return [chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26) for i in range(n)]
