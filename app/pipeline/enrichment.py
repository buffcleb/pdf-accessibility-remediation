"""AI enrichment: alt text, table structure and reading order, applied onto the analysis model."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from app.ai.base import AIError, AIProvider
from app.config import get_settings
from app.pipeline.analyzer import render_png
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, point_distance
from app.pipeline.structure import needs_ai_reading_order

log = logging.getLogger(__name__)

MAX_IMAGES = 300
MAX_TABLES = 100
MAX_ORDER_PAGES = 200
MAX_ORDER_REGIONS = 60  # beyond this the prompt outgrows small local models' context; heuristic order is used


@dataclass
class EnrichmentStats:
    images_described: int = 0
    images_decorative: int = 0
    images_failed: int = 0
    tables_analyzed: int = 0
    layout_tables: set[str] = field(default_factory=set)
    reading_order_pages: int = 0
    findings: list[str] = field(default_factory=list)
    # Specialist notation read by the AI: {"page", "bbox", "kind", "reason", "spoken", "latex", "confidence"}
    notation: list[dict] = field(default_factory=list)
    notation_rejected: int = 0
    # OCR lines corrected or transcribed by the AI: {"page", "bbox", "mode", "before", "after", "changes"}
    ocr: list[dict] = field(default_factory=list)
    ocr_rejected: int = 0


def _context(page: PageAnalysis, img: ImageRegion) -> str:
    cx, cy = (img.bbox[0] + img.bbox[2]) / 2, (img.bbox[1] + img.bbox[3]) / 2
    near = sorted(page.blocks, key=lambda b: point_distance(b.bbox, cx, cy))[:4]
    return " ".join(b.text for b in near)[:1500]


async def enrich(ai: AIProvider, pdf_path: Path, analysis: DocumentAnalysis, dpi: int = 150,
                 notation: bool = True, stats: EnrichmentStats | None = None) -> EnrichmentStats:
    """notation: also ask the AI to read specialist notation the layout rules can't (math, chemistry,
    medical, engineering)."""
    stats = stats or EnrichmentStats()
    doc = fitz.open(pdf_path)
    try:
        await asyncio.gather(
            _images(ai, doc, analysis, stats, dpi),
            _tables(ai, doc, analysis, stats, dpi),
            _reading_order(ai, doc, analysis, stats),
        )
        # After reading order: joining equation fragments changes the page's block list.
        if notation and get_settings().ai_notation_enabled:
            await _notation(ai, doc, analysis, stats)
    finally:
        doc.close()
    return stats


def _notation_regions(page: PageAnalysis) -> list[list]:
    """Groups of blocks to read together. Display equations are often extracted as several fragments
    (a piece with an unreadable symbol, the rest beside or under it): neighboring formula fragments
    are read as one equation when nothing else lies between them."""
    from app.pipeline.models import bbox_intersection, bbox_union
    from app.pipeline.notation import need

    cands = {id(b) for b in page.blocks if need(b) is not None}
    pool = [b for b in page.blocks if id(b) in cands or b.role == "Formula"]
    groups: list[list] = []
    for b in sorted(pool, key=lambda b: (b.bbox[1], b.bbox[0])):
        pad = 0.8 * max(b.font_size or 10.0, 6.0)
        grown = (b.bbox[0] - pad, b.bbox[1] - pad, b.bbox[2] + pad, b.bbox[3] + pad)
        touching = [g for g in groups if any(bbox_intersection(grown, o.bbox) > 0 for o in g)]
        merged = [b] + [o for g in touching for o in g]
        groups = [g for g in groups if g not in touching] + [merged]
    out = []
    for g in groups:
        if not any(id(b) in cands for b in g):
            continue
        g.sort(key=lambda b: (round(b.bbox[1] / 4), b.bbox[0]))
        if len(g) > 1:
            union = bbox_union([b.bbox for b in g])
            others = [o for o in page.blocks if o not in g and o.role != "Artifact"
                      and bbox_intersection(union, o.bbox) > 0.3 * max(1.0, (o.bbox[2] - o.bbox[0]) * (o.bbox[3] - o.bbox[1]))]
            if others:  # something else sits among the pieces: read them one by one
                out += [[b] for b in g if id(b) in cands]
                continue
        out.append(g)
    return out


async def _notation(ai, doc, analysis, stats) -> None:
    """Blocks whose notation the layout rules can't read reliably get the AI's reading, if its
    transcription matches the characters on the page."""
    from app.pipeline.models import append_block, bbox_union
    from app.pipeline.notation import consistent, need, readable

    limit = get_settings().ai_notation_max
    todo = [(p, g) for p in analysis.pages for g in _notation_regions(p)]
    todo = [(p, g) for p, g in todo if readable(" ".join(b.text for b in g)) >= 2]
    if len(todo) > limit:
        stats.findings.append(f"{len(todo) - limit} region(s) of specialist notation beyond the limit of {limit} "
                              "kept the layout rules' reading")
        todo = todo[:limit]

    async def one(page: PageAnalysis, group: list) -> None:
        why = next(n for b in group if (n := need(b)) is not None)
        text = " ".join(b.text for b in group)
        x0, y0, x1, y1 = bbox_union([b.bbox for b in group])
        pad = max(2.0, 0.3 * max(b.font_size or 10 for b in group))
        png = render_png(doc, page.number, dpi=200, clip=(x0 - pad, y0 - pad, x1 + pad, y1 + pad), max_px=1600)
        where = f"page {page.number + 1}"
        try:
            res = await ai.read_notation(png, page.number, text, why.kind)
        except (AIError, ValueError) as exc:
            stats.findings.append(f"{where}: AI reading of {why.kind} notation failed ({exc}); layout rules used")
            return
        ok, problem = consistent(text, res["transcription"], res.get("latex"))
        if not ok:
            stats.notation_rejected += 1
            stats.findings.append(f"{where}: AI reading of {why.kind} notation not used ({problem}); layout rules used")
            return
        head = group[0]
        if len(group) > 1:
            # One equation, one Formula: the fragments join the first piece.
            for b in group[1:]:
                append_block(head, b)
                page.blocks.remove(b)
                if b.id in page.reading_order:
                    page.reading_order.remove(b.id)
            head.role = "Formula"
        head.actual_text = res["spoken"]
        stats.notation.append({"page": page.number, "bbox": list(head.bbox), "role": head.role,
                               "kind": res["kind"], "reason": why.reason,
                               "spoken": res["spoken"], "latex": res.get("latex"), "pieces": len(group),
                               "confidence": res["confidence"], "text": text[:200]})
        stats.findings.append(f"{where}: {why.kind} notation read by AI ({why.reason}"
                              + (f", {len(group)} pieces read as one equation" if len(group) > 1 else "") + "); check it")

    await asyncio.gather(*(one(p, g) for p, g in todo))


async def _images(ai, doc, analysis, stats, dpi) -> None:
    todo = [(p, i) for p in analysis.pages for i in p.images if not i.decorative][:MAX_IMAGES]
    # The same image XObject (logos, icons) is described once and reused.
    by_xref: dict[int, asyncio.Task] = {}

    async def describe(page: PageAnalysis, img: ImageRegion) -> dict:
        png = render_png(doc, page.number, dpi=dpi, clip=img.bbox, max_px=1200)
        return await ai.describe_image(png, page.number, _context(page, img))

    jobs = []
    for page, img in todo:
        if img.xref and img.xref in by_xref:
            jobs.append((img, by_xref[img.xref]))
            continue
        task = asyncio.ensure_future(describe(page, img))
        if img.xref:
            by_xref[img.xref] = task
        jobs.append((img, task))

    for img, task in jobs:
        try:
            res = await task
        except (AIError, ValueError) as exc:
            stats.images_failed += 1
            stats.findings.append(f"page {img.page + 1}: alt text generation failed ({exc})")
            continue
        if res["decorative"]:
            img.decorative = True
            stats.images_decorative += 1
            continue
        img.alt = res["alt"]
        img.kind = "formula" if res["kind"] == "formula" else res["kind"]
        img.latex = res.get("latex")
        stats.images_described += 1


async def _tables(ai, doc, analysis, stats, dpi) -> None:
    tables = [t for p in analysis.pages for t in p.tables][:MAX_TABLES]

    async def one(t: TableRegion) -> None:
        grid = [["" for _ in range(t.n_cols)] for _ in range(t.n_rows)]
        for c in t.cells:
            if c.row < t.n_rows and c.col < t.n_cols:
                grid[c.row][c.col] = c.text
        png = render_png(doc, t.page, dpi=dpi, clip=t.bbox, max_px=1100)
        try:
            try:
                res = await ai.analyze_table(png, t.page, grid)
            except AIError as exc:
                msg = str(exc).lower()
                if not any(k in msg for k in ("context", "mtmd", "too large", "too many tokens", "maximum")):
                    raise
                # Server limit (context size / image too large): retry once with a smaller request.
                small = render_png(doc, t.page, dpi=dpi, clip=t.bbox, max_px=640)
                res = await ai.analyze_table(small, t.page, [row[:10] for row in grid[:15]])
                stats.findings.append(f"page {t.page + 1}: table re-sent at reduced size after a server limit")
        except (AIError, ValueError) as exc:
            stats.findings.append(f"page {t.page + 1}: table analysis failed ({exc}); first row assumed to be headers")
            return
        stats.tables_analyzed += 1
        t.ai_used = True
        if res["is_layout_table"]:
            stats.layout_tables.add(t.id)
            return
        if res["header_rows"] == 0 and t.header_evidence:
            # Small vision models often answer 0; keep the header row the layout clearly shows.
            stats.findings.append(f"page {t.page + 1}: AI reported no header row; kept row 1 as headers "
                                  "(labels above numeric data)")
            res["header_rows"] = 1
        t.header_rows = min(res["header_rows"], t.n_rows)
        t.header_cols = min(res["header_cols"], t.n_cols)
        t.summary = res["summary"]
        cells = {(c.row, c.col): c for c in t.cells}
        for span in res["spans"]:
            try:
                r0, c0 = int(span["row"]), int(span["col"])
                rs = max(1, int(span.get("row_span", 1) or 1))
                cs = max(1, int(span.get("col_span", 1) or 1))
            except (KeyError, TypeError, ValueError):
                continue
            cell = cells.get((r0, c0))
            if not cell:
                continue
            # A merge cannot cover another cell that has its own text: the model is wrong there.
            covered = [cells.get((r, c)) for r in range(r0, r0 + rs) for c in range(c0, c0 + cs) if (r, c) != (r0, c0)]
            if any(x is not None and x.text for x in covered):
                stats.findings.append(f"page {t.page + 1}: ignored AI merge at row {r0 + 1}, column {c0 + 1} "
                                      "because it would cover cells with their own text")
                continue
            cell.row_span, cell.col_span = max(cell.row_span, rs), max(cell.col_span, cs)
        for c in t.cells:
            c.is_header = c.row < t.header_rows or c.col < t.header_cols

    await asyncio.gather(*(one(t) for t in tables))


async def _reading_order(ai, doc, analysis, stats) -> None:
    pages = [p for p in analysis.pages if needs_ai_reading_order(p) and len(p.reading_order) > 1][:MAX_ORDER_PAGES]

    async def one(p: PageAnalysis) -> None:
        if len(p.reading_order) > MAX_ORDER_REGIONS:
            stats.findings.append(f"page {p.number + 1}: {len(p.reading_order)} regions; too many for AI reading "
                                  "order, layout heuristic used")
            return
        regions = []
        for eid in p.reading_order:
            el = p.element(eid)
            text = getattr(el, "text", None) or ("[table]" if isinstance(el, TableRegion) else "[figure]")
            regions.append({"id": eid, "bbox": el.bbox, "text": text})
        png = render_png(doc, p.number, dpi=100, max_px=1400)
        try:
            order = await ai.determine_reading_order(png, p.number, p.width, p.height, regions)
        except (AIError, ValueError) as exc:
            stats.findings.append(f"page {p.number + 1}: AI reading order unavailable ({exc}); layout heuristic used")
            return
        if order != p.reading_order:
            stats.findings.append(f"page {p.number + 1}: reading order adjusted by AI")
        p.reading_order = order
        p.reading_order_source = "ai"
        stats.reading_order_pages += 1

    await asyncio.gather(*(one(p) for p in pages))
