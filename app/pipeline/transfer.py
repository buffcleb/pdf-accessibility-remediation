"""Carry AI results from the original analysis over to a re-analyzed (flattened) document,
so the modified output does not need a second round of AI calls."""
from __future__ import annotations

from app.pipeline.models import BBox, DocumentAnalysis, PageAnalysis, bbox_area, bbox_intersection
from app.pipeline.structure import heuristic_reading_order


def _iou(a: BBox, b: BBox) -> float:
    inter = bbox_intersection(a, b)
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union else 0.0


def transfer_enrichment(orig: DocumentAnalysis, new: DocumentAnalysis, flattened: set[int],
                        orig_layout_tables: set[str], notation: list[dict] | None = None) -> set[str]:
    """notation: the AI's readings of specialist notation (EnrichmentStats.notation), re-applied to the
    re-analyzed blocks."""
    from app.pipeline.notation import apply_reading

    layout_tables: set[str] = set()
    for op, np in zip(orig.pages, new.pages):
        _transfer_roles(op, np)
        for reading in notation or []:
            if reading["page"] == np.number:
                apply_reading(np, reading)
        np.reading_order = heuristic_reading_order(np)
        page_area = np.width * np.height
        for img in np.images:
            if np.number in flattened and bbox_area(img.bbox) >= 0.9 * page_area:
                img.decorative = True  # flattened page background
                continue
            best = max(op.images, key=lambda o: _iou(o.bbox, img.bbox), default=None)
            if best is not None and _iou(best.bbox, img.bbox) > 0.5:
                img.alt, img.decorative, img.kind, img.latex = best.alt, best.decorative, best.kind, best.latex
        for t in np.tables:
            best = max(op.tables, key=lambda o: _iou(o.bbox, t.bbox), default=None)
            if best is None or _iou(best.bbox, t.bbox) <= 0.5:
                continue
            if best.id in orig_layout_tables:
                layout_tables.add(t.id)
            t.header_rows, t.header_cols, t.summary = best.header_rows, best.header_cols, best.summary
            spans = {(c.row, c.col): (c.row_span, c.col_span) for c in best.cells}
            for c in t.cells:
                c.row_span, c.col_span = spans.get((c.row, c.col), (1, 1))
                c.is_header = c.row < t.header_rows or c.col < t.header_cols
        if op.reading_order_source == "ai":
            np.reading_order = _map_order(op, np)
            np.reading_order_source = "ai (transferred)"
    return layout_tables


def _transfer_roles(op: PageAnalysis, np: PageAnalysis) -> None:
    """Flattened text is redrawn in one regular-weight font, so bold-based heading detection no
    longer works on it. The roles found on the original page (headings, list items, page
    furniture) are carried over to the matching re-extracted blocks."""
    for b in np.blocks:
        area = bbox_area(b.bbox) or 1.0
        best = max(op.blocks, key=lambda o: bbox_intersection(o.bbox, b.bbox), default=None)
        if best is None or bbox_intersection(best.bbox, b.bbox) < 0.5 * min(area, bbox_area(best.bbox) or 1.0):
            continue
        b.role, b.list_label, b.artifact_type = best.role, best.list_label, best.artifact_type
        b.bold, b.font_size = best.bold, best.font_size


def _map_order(op: PageAnalysis, np: PageAnalysis) -> list[str]:
    rank = {eid: i for i, eid in enumerate(op.reading_order)}
    fallback = {eid: i for i, eid in enumerate(np.reading_order)}

    def key(eid: str) -> tuple[float, int]:
        el = np.element(eid)
        best, best_ov = None, 0.0
        for oid in op.reading_order:
            o = op.element(oid)
            ov = bbox_intersection(el.bbox, o.bbox) if o is not None else 0.0
            if ov > best_ov:
                best, best_ov = oid, ov
        return (rank[best] if best is not None else len(rank) + fallback[eid], fallback[eid])

    return sorted(np.reading_order, key=key)
