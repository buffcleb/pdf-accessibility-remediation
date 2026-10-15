"""Marks explained by a legend: a slide's "▲ Beta  ◆ Select  ✦ AI Capabilities  ● Regulatory" key, and the same small
shapes placed beside items. The marks beside items carry meaning (this item is in beta), so their alt text is the
legend's label: "Beta". An AI asked to describe one sees "a blue triangle" and often calls it decorative.

A legend entry is a small filled shape followed on the same row by a short label (up to four words). Marks match an
entry by fill color, outline (the sequence of path segments) and size.
"""
from __future__ import annotations

import pymupdf as fitz

MAX_MARK = 24.0  # points: bigger than this is a picture, not a mark
MAX_LABEL_WORDS = 4
GAP = 14.0  # the label starts within this distance of the shape


def _signature(d: dict) -> tuple | None:
    fill = d.get("fill")
    r = d.get("rect")
    if fill is None or r is None or not (3 <= r.width <= MAX_MARK and 3 <= r.height <= MAX_MARK):
        return None
    kinds = "".join(it[0] for it in d.get("items", []))
    return tuple(round(c, 2) for c in fill), kinds, round(r.width), round(r.height)


def _same(a: tuple, b: tuple) -> bool:
    return a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) <= 1 and abs(a[3] - b[3]) <= 1


class Legends:
    """Per page, the legend entries and the small shapes; built lazily from the PDF."""

    def __init__(self, pdf_path):
        self.path = pdf_path
        self._doc = None
        self._pages: dict[int, tuple[list, list]] = {}

    def _page(self, n: int) -> tuple[list, list]:
        if n not in self._pages:
            if self._doc is None:
                self._doc = fitz.open(self.path)
            page = self._doc[n]
            shapes = [(d["rect"], sig) for d in page.get_drawings() if (sig := _signature(d)) is not None]
            # The same word drawn twice at the same spot (a hidden copy under a shape) counts once
            words, seen = [], set()
            for w in page.get_text("words"):
                key = (w[4], round(w[0]), round(w[1]))
                if key not in seen:
                    seen.add(key)
                    words.append(w)
            entries = []
            for rect, sig in shapes:
                cy = (rect.y0 + rect.y1) / 2
                row = sorted((w for w in words if w[1] <= cy <= w[3] and 0 <= w[0] - rect.x1 <= GAP * 6),
                             key=lambda w: w[0])
                if not row or row[0][0] - rect.x1 > GAP:
                    continue
                label, last = [row[0][4]], row[0]
                for w in row[1:]:
                    if w[0] - last[2] > 6:  # a gap: the next entry's label, or something else
                        break
                    label.append(w[4])
                    last = w
                if len(label) > MAX_LABEL_WORDS:
                    continue
                # A legend's label ends its entry: next on the row is nothing, or the next entry's shape (an item's
                # bold name is followed by the rest of its sentence)
                after = [w for w in row if w[0] > last[2] and w[0] - last[2] <= 60]
                shape_next = any(abs((r.y0 + r.y1) / 2 - cy) <= 4 and 0 <= r.x0 - last[2] <= 60 for r, _ in shapes)
                if after and not (shape_next and min(w[0] for w in after) > min(
                        r.x0 for r, _ in shapes if abs((r.y0 + r.y1) / 2 - cy) <= 4 and r.x0 > last[2])):
                    continue
                entries.append((rect, sig, " ".join(label)))
            # A legend is a row of at least two entries
            entries = [e for e in entries
                       if sum(1 for o in entries if abs((o[0].y0 + o[0].y1) / 2 - (e[0].y0 + e[0].y1) / 2) <= 4) >= 2]
            self._pages[n] = (shapes, entries)
        return self._pages[n]

    def label(self, page: int, bbox) -> str | None:
        """The legend's label for the mark at bbox, or None (not a mark, or no legend explains it). A legend's own
        shape returns None: its label is right beside it."""
        shapes, entries = self._page(page)
        if not entries:
            return None
        box = fitz.Rect(bbox)
        for rect, sig in shapes:
            if abs(rect.x0 - box.x0) > 2 or abs(rect.y0 - box.y0) > 2 or abs(rect.x1 - box.x1) > 2:
                continue
            if any(r == rect for r, _, _ in entries):
                return None
            labels = {lbl for _, s, lbl in entries if _same(s, sig)}
            return labels.pop() if len(labels) == 1 else None
        return None

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
