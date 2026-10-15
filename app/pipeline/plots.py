"""Graphs drawn as hundreds or thousands of tiny pieces.

Plotting programs and TeX packages draw a curve as a string of dots, each a picture a fraction of a point across.
One by one they are specks, which the analysis rightly calls decoration, so the whole graph was skipped by a
screen reader. Here a region densely covered by such specks becomes one figure: with the rules that run through it
(its axes) and the short labels on and around it (axis values, a curve's name). It is then described and tagged
like any other figure, every piece inside it, and the page isn't changed.

A caption or a sentence beside the graph stays text. A dotted rule (specks in a line) isn't a graph.
"""
from __future__ import annotations

import re

from app.pipeline.models import BBox, ImageRegion, TextBlock, bbox_area, bbox_intersection, bbox_union

_SPECK = 6.0     # points: a picture smaller than this each way is a speck
_CELL = 10.0     # the grid specks are gathered on
_REACH = 2       # cells: specks this near belong together (two curves of one graph, a curve and its axis)
_MIN_SPECKS = 60
_MIN_SIZE = (36.0, 24.0)  # a region narrower or flatter than this is a dotted rule
_LABEL_PAD = 14.0
_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|table|chart|graph|plate|example)\s*\d", re.IGNORECASE)


def _is_label(block: TextBlock) -> bool:
    """A value on an axis, a curve's name: short, and not a caption or a sentence."""
    text = (block.text or "").strip()
    if not text or _CAPTION.match(text):
        return False
    return len(text.split()) <= 4 and len(text) <= 40


def find_dense_regions(images: list[ImageRegion], blocks: list[TextBlock], width: float, height: float) -> list[BBox]:
    """Boxes of the regions densely covered by specks, each grown to take in its rules and labels."""
    specks = [i for i in images if max(i.bbox[2] - i.bbox[0], i.bbox[3] - i.bbox[1]) < _SPECK]
    if len(specks) < _MIN_SPECKS:
        return []
    cells: dict[tuple[int, int], list[ImageRegion]] = {}
    for s in specks:
        cells.setdefault((int(s.bbox[0] // _CELL), int(s.bbox[1] // _CELL)), []).append(s)
    seen: set[tuple[int, int]] = set()
    boxes: list[BBox] = []
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        todo, members = [start], []
        while todo:
            cx, cy = todo.pop()
            members += cells[(cx, cy)]
            for dx in range(-_REACH, _REACH + 1):
                for dy in range(-_REACH, _REACH + 1):
                    n = (cx + dx, cy + dy)
                    if n in cells and n not in seen:
                        seen.add(n)
                        todo.append(n)
        if len(members) < _MIN_SPECKS:
            continue
        box = bbox_union([m.bbox for m in members])
        if box[2] - box[0] < _MIN_SIZE[0] or box[3] - box[1] < _MIN_SIZE[1]:
            continue
        boxes.append(box)
    out: list[BBox] = []
    speck_ids = {id(s) for s in specks}
    rules = [_fat(i.bbox) for i in images
             if id(i) not in speck_ids and min(i.bbox[2] - i.bbox[0], i.bbox[3] - i.bbox[1]) < _SPECK]
    for box in boxes:
        for _ in range(3):
            grown = (box[0] - _LABEL_PAD, box[1] - _LABEL_PAD, box[2] + _LABEL_PAD, box[3] + _LABEL_PAD)
            # Rules through the graph (axes, ticks) and the labels on and around it
            more = [r for r in rules if bbox_intersection(r, box) > 0]
            more += [b.bbox for b in blocks if _is_label(b) and bbox_intersection(b.bbox, grown) > 0]
            new = bbox_union([box] + more)
            new = (max(0.0, new[0]), max(0.0, new[1]), min(width, new[2]), min(height, new[3]))
            if new == box:
                break
            box = new
        out.append(box)
    # Graphs whose boxes run into each other are one figure
    merged: list[BBox] = []
    for box in sorted(out, key=lambda b: (b[1], b[0])):
        hit = next((n for n, m in enumerate(merged) if bbox_intersection(m, box) > 0), None)
        if hit is None:
            merged.append(box)
        else:
            merged[hit] = bbox_union([merged[hit], box])
    return [b for b in merged if bbox_area(b) < 0.9 * width * height]


def _fat(bbox: BBox) -> BBox:
    """A hairline has no area to intersect with: give it half a point each way."""
    return (bbox[0] - 0.5, bbox[1] - 0.5, bbox[2] + 0.5, bbox[3] + 0.5)


def add_to_page(page, boxes: list[BBox]) -> list[ImageRegion]:
    """Each region becomes one figure holding the specks, rules and labels inside it."""
    made = []
    for n, box in enumerate(boxes):
        inside = [b for b in page.blocks if bbox_intersection(b.bbox, box) >= 0.8 * (bbox_area(b.bbox) or 1.0)]
        labels = " ".join(b.text.strip() for b in sorted(inside, key=lambda b: (b.bbox[1], b.bbox[0])) if b.text.strip())
        made.append(ImageRegion(id=f"p{page.number}g{n}", page=page.number, bbox=box, xref=0, kind="vector",
                                holds_pieces=True, labels=labels[:400] or None))
        page.blocks = [b for b in page.blocks if b not in inside]

    def within(bbox: BBox) -> bool:
        probe = _fat(bbox)
        return any(bbox_intersection(probe, _fat(_fat(b))) >= 0.8 * bbox_area(probe) for b in boxes)

    page.images = [i for i in page.images if not within(i.bbox)] + made
    return made
