"""Maps a content unit's on-page bbox to the logical element it belongs to."""
from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.models import (
    BBox,
    PageAnalysis,
    bbox_area,
    bbox_contains_point,
    bbox_intersection,
    point_distance,
)

ARTIFACT_LAYOUT = ("artifact:Layout", "Artifact")
# A decorative picture (a shape behind a label, a shadow): unlike a plain Layout answer, which can also mean
# "nothing matched", this one says what the content is, so orphaned tagged content may be made an artifact on it
ARTIFACT_BACKGROUND = ("artifact:Background", "Artifact")

_MC_TAG = {"LI": "LBody", "Caption": "P", "TOCI": "P"}


@dataclass
class _T:
    key: str
    tag: str
    bbox: BBox


# Blocks whose footnote marks are tagged as References inside them (others say "footnote 1" in their read-aloud text)
MARK_ROLES = {"P", "H1", "H2", "H3", "H4", "H5", "H6", "LI"}


def mark_key(block_id: str, n: int) -> str:
    """The target of the n-th raised mark of a block."""
    return f"{block_id}~m{n}"


class PageResolver:
    def __init__(self, page: PageAnalysis, layout_tables: set[str] | None = None):
        self.page = page
        self.unmatched_key = f"p{page.number}:unmatched"
        self.text: list[_T] = []
        self.sizes = {b.id: b.font_size for b in page.blocks}
        for b in page.blocks:
            if b.role == "Artifact":
                self.text.append(_T("artifact:" + (b.artifact_type or "Pagination"), "Artifact", b.bbox))
            else:
                self.text.append(_T(b.id, _MC_TAG.get(b.role, b.role), b.bbox))
        # A footnote's mark in a paragraph, heading or list item is a target of its own (the smallest box it lies in), so its
        # glyphs are marked apart and can be tagged as a Reference to the note (struct_tree, mark_key)
        for b in page.blocks:
            if b.role in MARK_ROLES:
                for i, (box, note) in enumerate(zip(b.mark_boxes, b.mark_notes)):
                    if note and box:
                        self.text.append(_T(mark_key(b.id, i), "Reference", box))
        self.cells: list[_T] = []
        self.tables: list[tuple[BBox, list[_T]]] = []
        for t in page.tables:
            cells = [
                _T(t.cell_target_id(c), "TH" if c.is_header else "TD", c.bbox)
                for c in t.cells
                if c.bbox is not None
            ]
            if layout_tables and t.id in layout_tables:
                cells = [_T(k.key, "P", k.bbox) for k in cells]
            self.cells += cells
            self.tables.append((t.bbox, cells))
        self.figures = [_T(i.id, "Figure", i.bbox) for i in page.images if not i.decorative]
        # Music examples: everything drawn inside one (staff lines as sliver images, note heads) is part of it
        self.music = [_T(i.id, "Figure", i.bbox) for i in page.images if i.kind == "music" and not i.decorative]
        # Figures made of many small pieces (music, graphs drawn dot by dot): every picture inside is part of them
        self.holders = [_T(i.id, "Figure", i.bbox) for i in page.images if i.holds_pieces and not i.decorative]
        self.decor = [i for i in page.images if i.decorative]
        self.images = page.images
        self._grid: dict[tuple[int, int], list[int]] | None = None  # images by 32-point square, made on first use
        self._first_xref: dict[int, int] = {}

    # --------------------------------------------------------------------- api
    def __call__(self, kind: str, bbox: BBox, xref: int) -> tuple[str, str]:
        if kind == "text":
            return self._text(bbox)
        if kind == "image":
            return self._image(bbox, xref)
        if kind == "path":
            return self._path(bbox)
        if kind == "form":
            return self._form(bbox)
        return ARTIFACT_LAYOUT

    def text_owner(self, bbox: BBox) -> str | None:
        """The text block or cell that clearly contains this glyph, or None (no guessing by distance)."""
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        hit = self._smallest_containing(self.cells + self.text, cx, cy, tol=0.5)
        return hit.key if hit else None

    # --------------------------------------------------------------------- kinds
    def _text(self, bbox: BBox) -> tuple[str, str]:
        if self.music:
            # Note heads and clefs are characters of a music font, whose boxes reach far above their ink: they
            # belong to the example their foot stands in, not to the paragraph their middle happens to be over
            from app.pipeline.music import in_music

            n = in_music([m.bbox for m in self.music], bbox)
            if n is not None:
                return self.music[n].key, self.music[n].tag
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        # The estimated advance can overshoot; also try the start of the run.
        sx = bbox[0] + min(4.0, (bbox[2] - bbox[0]) / 2)
        for x in (cx, sx):
            hit = self._line_containing(bbox, x, cy, tol=2.0)
            if hit:
                return hit.key, hit.tag
        for tb, cells in self.tables:
            if bbox_contains_point(tb, cx, cy, 2.0) and cells:
                c = min(cells, key=lambda t: point_distance(t.bbox, cx, cy))
                return c.key, c.tag
        hit = self._smallest_containing(self.figures, cx, cy, tol=1.0)
        if hit:
            return hit.key, hit.tag
        pool = self.text + self.cells
        if pool:
            near = min(pool, key=lambda t: point_distance(t.bbox, sx, cy))
            if point_distance(near.bbox, sx, cy) < 36:
                return near.key, near.tag
        # Never hide real text as an artifact: it gets its own paragraph.
        return self.unmatched_key, "P"

    _CELL = 32.0

    def _images_near(self, bbox: BBox, xref: int = 0) -> list:
        """The images that could match a box: the ones that touch it, and the first drawn from the same object (the
        only one of those that can win without touching it). In page order, as a scan of them all would meet them.
        A page of printed music has thousands of images; comparing each drawn one with all of them took minutes."""
        if len(self.images) <= 64:
            return self.images
        c = self._CELL
        if self._grid is None:
            self._grid = {}
            for n, img in enumerate(self.images):
                b = img.bbox
                for gx in range(int(b[0] // c), int(b[2] // c) + 1):
                    for gy in range(int(b[1] // c), int(b[3] // c) + 1):
                        self._grid.setdefault((gx, gy), []).append(n)
                if img.xref:
                    self._first_xref.setdefault(img.xref, n)
        hits: set[int] = set()
        for gx in range(int(bbox[0] // c), int(bbox[2] // c) + 1):
            for gy in range(int(bbox[1] // c), int(bbox[3] // c) + 1):
                hits.update(self._grid.get((gx, gy), ()))
        if xref and xref in self._first_xref:
            hits.add(self._first_xref[xref])
        return [self.images[n] for n in sorted(hits)]

    def _image(self, bbox: BBox, xref: int) -> tuple[str, str]:
        best, best_score = None, 0.0
        area = bbox_area(bbox) or 1.0
        for img in self._images_near(bbox, xref):
            ov = bbox_intersection(bbox, img.bbox) / max(area, bbox_area(img.bbox) or 1.0)
            score = ov + (0.5 if xref and img.xref == xref else 0.0)
            if score > best_score:
                best, best_score = img, score
        if best is None or best_score < 0.3 or (best.decorative and self.holders):
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            hit = self._smallest_containing(self.holders, cx, cy, tol=0.5) if self.holders else None
            # A piece lies inside its figure. A picture that merely has its middle there does not: a flattened
            # page's image has its middle in whatever figure sits mid-page, and made that figure the whole page
            if hit and bbox_intersection(bbox, hit.bbox) >= 0.5 * area:
                return hit.key, hit.tag
        if best is None or best_score < 0.3:
            return ARTIFACT_LAYOUT
        if best.decorative:
            return ARTIFACT_LAYOUT
        return best.id, "Figure"

    def _path(self, bbox: BBox) -> tuple[str, str]:
        area = bbox_area(bbox)
        for f in self.figures:
            if area == 0:
                if bbox_contains_point(f.bbox, bbox[0], bbox[1], 1.0):
                    return f.key, f.tag
            elif bbox_intersection(bbox, f.bbox) / area >= 0.7:
                return f.key, f.tag
        return ARTIFACT_LAYOUT  # rules, borders, backgrounds, table grid lines

    def _form(self, bbox: BBox) -> tuple[str, str]:
        # A form that is essentially one picture (slides draw a shape and its shadow as a form) is that picture:
        # an artifact if it's decoration, else its Figure. Overlap alone would hand it to whatever it touches most:
        # a line of text, or a photo whose corner it crosses.
        area = bbox_area(bbox) or 1.0
        pic, pic_ov = None, 0.0
        for img in self._images_near(bbox):
            ov = bbox_intersection(bbox, img.bbox) / max(area, bbox_area(img.bbox) or 1.0)
            if ov > pic_ov:
                pic, pic_ov = img, ov
        if pic is not None and pic_ov >= 0.5:
            return ARTIFACT_BACKGROUND if pic.decorative else (pic.id, "Figure")
        best, best_ov = None, 0.0
        for t in self.text + self.cells + self.figures:
            ov = bbox_intersection(bbox, t.bbox)
            if ov > best_ov:
                best, best_ov = t, ov
        return (best.key, best.tag) if best else ARTIFACT_LAYOUT

    def _line_containing(self, glyph: BBox, x: float, y: float, tol: float) -> _T | None:
        """The smallest box the point lies in; but a subscript in small print at the foot of a line ("N2H4" in a
        choice) that reaches into the box of the line below, set a little closer than its print, belongs to the line
        above it when that line's box holds it and the smaller one doesn't. Table cells keep the smallest: a cell's box
        is its own, not a line's."""
        best = self._smallest_containing(self.cells + self.text, x, y, tol)
        if best is None or any(best is c for c in self.cells):
            return best
        h = glyph[3] - glyph[1]
        if h <= 0:
            return best

        def held(t: _T) -> float:
            return max(0.0, min(t.bbox[3], glyph[3]) - max(t.bbox[1], glyph[1])) / h

        def stacked(t: _T) -> bool:  # one over the other, overlapping a little
            a, b = t.bbox, best.bbox
            across = min(a[2], b[2]) - max(a[0], b[0])
            down = min(a[3], b[3]) - max(a[1], b[1])
            return across > 0.5 * min(a[2] - a[0], b[2] - b[0]) and 0 < down < 0.5 * min(a[3] - a[1], b[3] - b[1])

        if held(best) >= 0.33:
            return best  # (a power at the top of its own line reaches into the one above by more than a third)
        # (small print for the line above: a run of a line's own text, or a tall sign, stays where it was)
        better = [t for t in self.text if t is not best and t.tag != "Artifact" and stacked(t) and held(t) >= 0.9
                  and t.bbox[1] < best.bbox[1] and h <= 0.8 * self.sizes.get(t.key, 0.0)
                  and bbox_contains_point(t.bbox, x, y, tol)]
        return min(better, key=lambda t: bbox_area(t.bbox)) if better else best

    @staticmethod
    def _smallest_containing(targets: list[_T], x: float, y: float, tol: float) -> _T | None:
        hits = [t for t in targets if bbox_contains_point(t.bbox, x, y, tol)]
        return min(hits, key=lambda t: bbox_area(t.bbox)) if hits else None
