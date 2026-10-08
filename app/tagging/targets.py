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
ARTIFACT_PAGINATION = ("artifact:Pagination", "Artifact")

_MC_TAG = {"LI": "LBody", "Caption": "P"}


@dataclass
class _T:
    key: str
    tag: str
    bbox: BBox


class PageResolver:
    def __init__(self, page: PageAnalysis, layout_tables: set[str] | None = None):
        self.page = page
        self.unmatched_key = f"p{page.number}:unmatched"
        self.text: list[_T] = []
        for b in page.blocks:
            if b.role == "Artifact":
                self.text.append(_T("artifact:" + (b.artifact_type or "Pagination"), "Artifact", b.bbox))
            else:
                self.text.append(_T(b.id, _MC_TAG.get(b.role, b.role), b.bbox))
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
        self.decor = [i for i in page.images if i.decorative]
        self.images = page.images

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
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        # The estimated advance can overshoot; also try the start of the run.
        sx = bbox[0] + min(4.0, (bbox[2] - bbox[0]) / 2)
        for x in (cx, sx):
            hit = self._smallest_containing(self.cells + self.text, x, cy, tol=2.0)
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

    def _image(self, bbox: BBox, xref: int) -> tuple[str, str]:
        best, best_score = None, 0.0
        area = bbox_area(bbox) or 1.0
        for img in self.images:
            ov = bbox_intersection(bbox, img.bbox) / max(area, bbox_area(img.bbox) or 1.0)
            score = ov + (0.5 if xref and img.xref == xref else 0.0)
            if score > best_score:
                best, best_score = img, score
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
        best, best_ov = None, 0.0
        for t in self.text + self.cells + self.figures:
            ov = bbox_intersection(bbox, t.bbox)
            if ov > best_ov:
                best, best_ov = t, ov
        return (best.key, best.tag) if best else ARTIFACT_LAYOUT

    @staticmethod
    def _smallest_containing(targets: list[_T], x: float, y: float, tol: float) -> _T | None:
        hits = [t for t in targets if bbox_contains_point(t.bbox, x, y, tol)]
        return min(hits, key=lambda t: bbox_area(t.bbox)) if hits else None
