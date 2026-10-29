"""AI enrichment: alt text, table structure and reading order, applied onto the analysis model."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from app.ai.base import AIError, AIProvider
from app.config import get_settings
from app.pipeline.analyzer import render_png
from app.pipeline.lists import label_kind
from app.pipeline.models import (DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock,
                                    point_distance)
from app.pipeline.structure import caption_pairs, needs_ai_reading_order

log = logging.getLogger(__name__)

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
    # ...by reason (numbers left out), for the job's metrics: the findings stop at 100, the counts don't
    notation_rejected_by: dict = field(default_factory=dict)
    # Scanned pages the AI was asked about (where their formulas are), and its boxes that held no OCR text (a model
    # can carry a pattern of boxes on into blank space; or the OCR missed the writing): nothing to read there
    scan_formula_pages: int = 0
    scan_formulas_without_text: int = 0
    dose_cells: int = 0  # table cells whose doses were put in words by rule
    # Limits this document reached (#312): {"setting", "value", "wanted", "effect"}, one per setting
    limits_hit: list = field(default_factory=list)

    def limit_hit(self, setting: str, value: int, wanted: int, effect: str, count: bool = False) -> None:
        """Record that a limit cut something short: setting is its name (the environment variable, or the constant),
        value what it is set to, wanted what this document would have used. count: a limit met once per page (or
        other unit): wanted counts the times, and adds up."""
        for x in self.limits_hit:
            if x["setting"] == setting:
                x["wanted"] = x["wanted"] + wanted if count else max(x["wanted"], wanted)
                return
        self.limits_hit.append({"setting": setting, "value": value, "wanted": wanted, "effect": effect,
                                **({"count": True} if count else {})})
    # OCR lines corrected or transcribed by the AI: {"page", "bbox", "mode", "before", "after", "changes"}
    ocr: list[dict] = field(default_factory=list)
    ocr_rejected: int = 0
    # Music examples: {"page", "bbox", "read" (by the music reading service), "measures", "notes", "full", "error"}
    music: list[dict] = field(default_factory=list)
    music_score: int = 0  # sheet music: files the whole piece was read into (one, or one per movement)
    math_formulas: int = 0  # formulas saved as MathML too (app/pipeline/mathml.py, #403)
    # The formulas with their MathML and MathCAT's reading (mathml.collect, read_by_rule), and how many of them the
    # two readings disagree on
    formulas: list[dict] = field(default_factory=list)
    math_disagree: int = 0
    math_picture_disagree: int = 0  # of them, how many the formula reading service's reading disagrees with
    math_embedded: int = 0  # of them, MathML attached to their tags in the PDF (a PDF 2.0 file, #403)


def render_slots() -> asyncio.Semaphore:
    """As many places as the provider makes calls at once. A picture is rendered inside one, right before its call:
    rendered outside, every picture of a document would be made up front and held while the calls queue."""
    return asyncio.Semaphore(get_settings().ai_concurrency)


def _context(page: PageAnalysis, img: ImageRegion) -> str:
    cx, cy = (img.bbox[0] + img.bbox[2]) / 2, (img.bbox[1] + img.bbox[3]) / 2
    cap_id = next((c for c, partner in caption_pairs(page).items() if partner == img.id), None)
    near = sorted((b for b in page.blocks if b.id != cap_id), key=lambda b: point_distance(b.bbox, cx, cy))[:4]
    text = " ".join(b.text for b in near)
    if cap_id is not None:
        # Tagged with the image, so a screen reader reads it as well as the alt text
        text = f"[The image's caption: {page.element(cap_id).text}] {text}"
    if img.labels:
        text = f"[Text inside the picture: {img.labels}] {text}"
    if img.drawing == "scheme":
        text = ("[The picture is a chemical structure or a reaction scheme drawn with lines. Say what it shows: each "
                "compound by name if it is named, else by its main features, and for a reaction what reacts, what is "
                "written on each arrow, and what forms, in the order the arrows give. Name only what is drawn.] " + text)
    elif img.drawing == "equation":
        text = ("[The picture is one displayed equation, set partly as text and partly as small pictures. Read it out "
                "whole, as it would be spoken, and give it as a formula.] " + text)
    return text[:1500]


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


async def read_notation(ai: AIProvider, pdf_path: Path, analysis: DocumentAnalysis,
                        stats: EnrichmentStats) -> EnrichmentStats:
    """Only the notation readings (and doses by rule), for a file whose other structure comes from elsewhere: Adobe's
    tags, which get the readings as ActualText."""
    doc = fitz.open(pdf_path)
    try:
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
        if len(g) > 1 and any(b.role in ("LI", "LBody", "TD", "TH", "Caption") for b in g):
            # List items and table cells are separate things however close they sit: answers "a." to "e." of a
            # question are not the fragments of one equation
            out += [[b] for b in g if id(b) in cands]
            continue
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
    from app.pipeline.models import bbox_union, merge_blocks
    from app.pipeline.notation import (DIVISION_NOT_WRITTEN, around_an_equation, consistent, consistent_scanned, doses_in_cells,
                                       drawn_fraction, scanned_math, in_a_sentence, join_broken_words, need, one_style,
                                       place_in_reading, readable, rejection_reason, say_doses, spans,
                                       spoken_consistent, with_its_surroundings)

    # Doses and prescription shorthand are put in words by rule: there is one right way to say "5 mg/mL", it needs
    # no picture, and an AI reading it from a scan once turned "mcg" into "meg"
    by_rule: set[int] = set()
    for page in analysis.pages:
        for b in page.blocks:
            why = need(b)
            if why is None or why.kind != "medical":
                continue
            by_rule.add(id(b))
            before = b.actual_text or b.text
            said = join_broken_words(say_doses(before))
            if said != before and spoken_consistent(b.text, said, b.actual_text or "")[0]:
                b.actual_text = said
                b.notation_reading = True
                stats.notation.append({"page": page.number, "bbox": list(b.bbox), "role": b.role, "kind": "medical",
                                       "reason": why.reason, "spoken": said, "latex": None, "pieces": 1,
                                       "by_rule": True, "confidence": 1.0, "text": b.text[:200]})
    cells = sum(doses_in_cells(page) for page in analysis.pages)
    stats.dose_cells = cells
    if by_rule or cells:
        n = sum(1 for x in stats.notation if x.get("by_rule"))
        stats.findings.append(f"{n} block(s) and {cells} table cell(s) of dosage or prescription notation put in words by rule (units by name, "
                              "shorthand such as \"q 6 h\" and \"BID\" in full, a dash between numbers as \"to\")")

    # Scanned pages whose OCR text has a garbled formula: the AI says where the page's formulas are, once a page
    await _scanned_formulas(ai, doc, analysis, stats)

    limit = get_settings().ai_notation_max
    todo = [(p, g) for p in analysis.pages for g in _notation_regions(p)]
    todo = [(p, g) for p, g in todo if not all(id(b) in by_rule for b in g)]
    todo = [(p, g) for p, g in todo if readable(" ".join(b.text for b in g)) >= 2]
    if len(todo) > limit:
        stats.limit_hit("AI_NOTATION_MAX", limit, len(todo), "regions of specialist notation kept the layout rules' reading")
        stats.findings.append(f"{len(todo) - limit} region(s) of specialist notation beyond the limit of {limit} "
                              "kept the layout rules' reading")
        todo = todo[:limit]

    span_readings: dict[tuple[str, str], asyncio.Task] = {}  # the same formula is asked about once a document
    slots = render_slots()

    async def read_span(page: PageAnalysis, block, kind: str, piece: str):
        """The AI's reading of one formula or dose inside a sentence, or None if it can't be found on the page,
        can't be read, or doesn't match."""
        pad = max(2.0, 0.3 * (block.font_size or 10))
        clip = fitz.Rect(block.bbox) + (-pad, -pad, pad, pad)
        rects = doc[page.number].search_for(piece, clip=clip)
        if not rects:
            return None
        r = rects[0]
        for other in rects[1:]:  # a formula broken over two lines
            if other.y0 - r.y1 < 2 * (block.font_size or 10):
                r |= other
        try:
            async with slots:
                png = render_png(doc, page.number, dpi=200, clip=(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad),
                                 max_px=1600)
                res = await ai.read_notation(png, page.number, piece, kind)
        except (AIError, ValueError):
            return None
        res["spoken"] = one_style(res["spoken"], kind if kind != "unreadable" else res["kind"])
        if not consistent(piece, res["transcription"], res.get("latex"))[0] or not spoken_consistent(piece, res["spoken"])[0]:
            return None
        return res

    async def within_sentence(page: PageAnalysis, block, why, found: list[tuple[int, int]]) -> None:
        """Only the formulas or doses in a sentence go to the AI. The sentence's own words stay exactly as
        printed: there is nothing for the AI to reword, and nothing to reject for leaving the sentence out."""
        text, where = block.text, f"page {page.number + 1}"
        results = []
        for a, b in found[:12]:
            piece = text[a:b].strip()
            key = (why.kind, piece)
            if key not in span_readings:
                span_readings[key] = asyncio.ensure_future(read_span(page, block, why.kind, piece))
            results.append(((a, b), await span_readings[key]))
        read = [(pos, res) for pos, res in results if res is not None]
        failed = len(results) - len(read)
        if failed:
            stats.notation_rejected += failed
            reason = "a formula or dose in a sentence couldn't be read or didn't match the page"
            stats.notation_rejected_by[reason] = stats.notation_rejected_by.get(reason, 0) + failed
        if not read:
            stats.findings.append(f"{where}: AI reading of {why.kind} notation not used ({failed} formula(s) or dose(s) "
                                  "in a sentence couldn't be read or didn't match the page); layout rules used")
            return
        # The rest of the sentence keeps the layout rules' reading, where they made one ("10 to the power of 23")
        spoken = block.actual_text or text
        for (a, b), res in sorted(read, reverse=True):
            said = res["spoken"].strip()
            if text[a:b].rstrip()[-1:] not in ".;?!,:":
                said = said.rstrip(".;?!,: ")  # punctuation the AI saw beside the formula is the sentence's
            if block.actual_text:
                a, b = place_in_reading(text, block.actual_text, a, b)
            spoken = spoken[:a] + " " + said + " " + spoken[b:]
        block.actual_text = join_broken_words(re.sub(r"\s+([.,;:?!)])", r"\1", re.sub(r"\s{2,}", " ", spoken)).strip())
        block.notation_reading = True
        stats.notation.append({"page": page.number, "bbox": list(block.bbox), "role": block.role,
                               "kind": why.kind, "reason": why.reason, "spoken": block.actual_text,
                               "latex": None, "pieces": 1, "within_sentence": len(read),
                               "confidence": min(res["confidence"] for _, res in read), "text": text[:200]})
        stats.findings.append(f"{where}: {why.kind} notation read by AI ({len(read)} formula(s) or dose(s) inside a "
                              "sentence" + (f", {failed} more left as printed" if failed else "") + "); check it")

    async def one(page: PageAnalysis, group: list) -> None:
        why = next(n for b in group if (n := need(b)) is not None)
        text = " ".join(b.text for b in group)
        scanned = any(b.scan_formula or scanned_math(b) for b in group)
        # (a scanned formula is read whole: its garbled OCR text can't be searched for on the page piece by piece)
        if len(group) == 1 and group[0].role != "Formula" and not scanned:
            found = spans(text, why.kind)
            if in_a_sentence(text, found):
                await within_sentence(page, group[0], why, found)
                return
        x0, y0, x1, y1 = bbox_union([b.bbox for b in group])
        pad = max(2.0, 0.3 * max(b.font_size or 10 for b in group))
        where = f"page {page.number + 1}"
        try:
            async with slots:
                png = render_png(doc, page.number, dpi=200, clip=(x0 - pad, y0 - pad, x1 + pad, y1 + pad), max_px=1600)
                res = await ai.read_notation(png, page.number, text, why.kind)
        except (AIError, ValueError) as exc:
            stats.findings.append(f"{where}: AI reading of {why.kind} notation failed ({exc}); layout rules used")
            return
        res["spoken"] = one_style(res["spoken"], why.kind if why.kind != "unreadable" else res["kind"])
        # The word before a displayed equation and the number after it are the page's, not the equation's: checked
        # apart, and put back around the reading
        lead, core, label = around_an_equation(text)
        if scanned:
            # OCR lost much of the formula: the reading is checked against what OCR did read, and its numbers
            # against the AI's own transcription of the image
            lead = label = ""
            ok, problem = consistent_scanned(text, res["transcription"], res.get("latex"))
            if ok:
                ok, problem = spoken_consistent(res["transcription"], res["spoken"])
        else:
            ok, problem = consistent(core, res["transcription"], res.get("latex"))
            if not ok and (lead or label):
                ok, problem = consistent(text, res["transcription"], res.get("latex"))
        res["spoken"] = with_its_surroundings(res["spoken"], lead, label)
        if ok and not scanned:
            rules = " ".join(b.actual_text or "" for b in group)
            ok, problem = spoken_consistent(text, res["spoken"], rules)
            if not ok and problem == DIVISION_NOT_WRITTEN and drawn_fraction(
                    doc[page.number], (x0, y0, x1, y1), max(b.font_size or 10 for b in group)):
                # A stacked fraction: its "over" is the bar the page draws
                ok, problem = spoken_consistent(text, res["spoken"], rules, fraction_drawn=True)
        if not ok:
            stats.notation_rejected += 1
            reason = rejection_reason(problem)
            stats.notation_rejected_by[reason] = stats.notation_rejected_by.get(reason, 0) + 1
            stats.findings.append(f"{where}: AI reading of {why.kind} notation not used ({problem}); layout rules used")
            return
        head = group[0]
        if len(group) > 1:
            # One equation, one Formula: the fragments join the first piece.
            merge_blocks(page, head, group[1:])
            head.role = "Formula"
        elif scanned:
            head.role = "Formula"  # a scanned formula's size could have made it a heading
        head.actual_text = res["spoken"]
        head.notation_reading = True
        stats.notation.append({"page": page.number, "bbox": list(head.bbox), "role": head.role,
                               "kind": res["kind"], "reason": why.reason,
                               "spoken": res["spoken"], "latex": res.get("latex"), "pieces": len(group),
                               "confidence": res["confidence"], "text": text[:200]})
        stats.findings.append(f"{where}: {why.kind} notation read by AI ({why.reason}"
                              + (f", {len(group)} pieces read as one equation" if len(group) > 1 else "") + "); check it")

    await asyncio.gather(*(one(p, g) for p, g in todo))


# At most this many scanned pages are asked where their formulas are (one call each)
SCAN_FORMULA_PAGES = 100


async def _scanned_formulas(ai, doc, analysis, stats) -> None:
    """On scanned pages where some OCR text looks like a garbled formula (and only there, so a scanned page of prose
    costs no extra call), ask the AI once where the page's displayed formulas are. The text blocks inside each box are
    marked, so the formulas the OCR text didn't give away are read too (see notation.need)."""
    from app.pipeline.models import bbox_intersection
    from app.pipeline.notation import scanned_math

    pages = [p for p in analysis.pages if any(scanned_math(b) for b in p.blocks)]
    if len(pages) > SCAN_FORMULA_PAGES:
        stats.limit_hit("SCAN_FORMULA_PAGES", SCAN_FORMULA_PAGES, len(pages),
                        "scanned pages weren't asked where their formulas are")
        pages = pages[:SCAN_FORMULA_PAGES]
    if not pages:
        return
    slots = render_slots()

    async def one(page: PageAnalysis) -> None:
        pdf_page = doc[page.number]
        if pdf_page.rotation:
            return  # boxes on the image wouldn't line up with the page's text without turning them
        try:
            async with slots:
                png = render_png(doc, page.number, dpi=110, max_px=1600)
                boxes = await ai.find_formulas(png, page.number)
        except (AIError, ValueError) as exc:
            stats.findings.append(f"page {page.number + 1}: the AI couldn't say where the formulas are ({exc})")
            return
        stats.scan_formula_pages += 1
        w, h = pdf_page.rect.width, pdf_page.rect.height
        for x0, y0, x1, y1 in boxes:
            box = (x0 * w, y0 * h, x1 * w, y1 * h)
            inside = [b for b in page.blocks if b.role != "Artifact" and b.text.strip()
                      and bbox_intersection(box, b.bbox) > 0.5 * max(1.0, (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]))]
            if not inside:
                stats.scan_formulas_without_text += 1
            for b in inside:
                b.scan_formula = True

    await asyncio.gather(*(one(p) for p in pages))
    found = sum(1 for p in pages for b in p.blocks if b.scan_formula)
    stats.findings.append(f"{stats.scan_formula_pages} scanned page(s) with garbled formulas: the AI found formulas in "
                          f"{found} text block(s)" + (f"; {stats.scan_formulas_without_text} of its boxes held no OCR text "
                                                       "(blank space, or writing the OCR missed)"
                                                       if stats.scan_formulas_without_text else ""))


async def _images(ai, doc, analysis, stats, dpi) -> None:
    # A music example the reading service read already has its alt text
    limit = get_settings().ai_images_max
    todo = [(p, i) for p in analysis.pages for i in p.images if not i.decorative and not i.alt]
    if len(todo) > limit:
        stats.limit_hit("AI_IMAGES_MAX", limit, len(todo), "figures weren't described by the AI and have no alt text")
        stats.findings.append(f"{len(todo) - limit} figure(s) beyond the limit of {limit} (AI_IMAGES_MAX) weren't "
                              f"described by the AI and have no alt text (the first is on page {todo[limit][0].number + 1})")
        todo = todo[:limit]
    # The same image XObject (logos, icons) is described once and reused.
    by_xref: dict[int, asyncio.Task] = {}

    slots = render_slots()

    async def describe(page: PageAnalysis, img: ImageRegion) -> dict:
        async with slots:
            png = render_png(doc, page.number, dpi=dpi, clip=img.bbox, max_px=1200)
            if img.drawing == "scheme":  # several compounds and steps to say: more room than a photograph gets
                return await ai.describe_image(png, page.number, _context(page, img), long=True)
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
        if res["decorative"] and not (img.drawing == "scheme" and res["alt"]):
            img.decorative = True
            stats.images_decorative += 1
            continue
        img.alt = res["alt"]
        # Found by its staves, or by its bonds, or by the number beside its line (an equation): that stands, whatever
        # the description calls it (a drawn structure is a picture of a molecule, not a formula to be read out
        # symbol by symbol)
        if img.kind != "music" and img.drawing not in ("scheme", "equation"):
            img.kind = "formula" if res["kind"] == "formula" else res["kind"]
        img.latex = res.get("latex")
        stats.images_described += 1


async def _tables(ai, doc, analysis, stats, dpi) -> None:
    tables = [t for p in analysis.pages for t in p.tables]
    limit = get_settings().ai_tables_max
    if len(tables) > limit:
        stats.limit_hit("AI_TABLES_MAX", limit, len(tables), "tables kept the layout rules' headers and structure")
        stats.findings.append(f"{len(tables) - limit} table(s) beyond the limit of {limit} (AI_TABLES_MAX) kept the "
                              f"layout rules' headers and structure (from page {tables[limit].page + 1} on)")
        tables = tables[:limit]

    slots = render_slots()

    async def ask(t: TableRegion, grid: list[list[str]]) -> dict:
        async with slots:
            png = render_png(doc, t.page, dpi=dpi, clip=t.bbox, max_px=1100)
            try:
                return await ai.analyze_table(png, t.page, grid)
            except AIError as exc:
                msg = str(exc).lower()
                if not any(k in msg for k in ("context", "mtmd", "too large", "too many tokens", "maximum")):
                    raise
                # Server limit (context size / image too large): retry once with a smaller request.
                small = render_png(doc, t.page, dpi=dpi, clip=t.bbox, max_px=640)
                res = await ai.analyze_table(small, t.page, [row[:10] for row in grid[:15]])
                stats.findings.append(f"page {t.page + 1}: table re-sent at reduced size after a server limit")
                return res

    async def one(t: TableRegion) -> None:
        grid = [["" for _ in range(t.n_cols)] for _ in range(t.n_rows)]
        for c in t.cells:
            if c.row < t.n_rows and c.col < t.n_cols:
                grid[c.row][c.col] = c.text
        try:
            res = await ask(t, grid)
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
            # A span can't reach past the table: a model's (or a planted) span of millions is clamped before anything
            # is built from it
            rs, cs = min(rs, t.n_rows - r0), min(cs, t.n_cols - c0)
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


def headings_first(page: PageAnalysis, order: list[str]) -> tuple[list[str], int]:
    """A heading introduces what is drawn under it: nothing below it in its column is read before it. Where the AI's
    order has that (a small model once read a guide's introduction before the title above it), the heading moves to
    just before the first such region. The rest of the AI's order stays. Returns the order and how many headings
    moved."""
    order = list(order)
    moved = 0
    for hid in [e for e in order if getattr(page.element(e), "role", "").startswith("H")]:
        h = page.element(hid)

        def under(eid: str) -> bool:
            el = page.element(eid)
            if el is None:
                return False
            b = el.bbox
            overlap = min(h.bbox[2], b[2]) - max(h.bbox[0], b[0])
            narrower = min(h.bbox[2] - h.bbox[0], b[2] - b[0])
            return b[1] >= h.bbox[3] - 2 and narrower > 0 and overlap >= 0.5 * narrower

        at = order.index(hid)
        first = next((i for i, eid in enumerate(order[:at]) if under(eid)), None)
        if first is not None:
            order.insert(first, order.pop(at))
            moved += 1
    return order, moved


def items_before_choices(page: PageAnalysis, order: list[str]) -> tuple[list[str], int]:
    """A numbered item comes before the lettered items under it ("17." before its "a.", "b."): where the AI's order
    reads one of them first (a small model once put a question's first choice ahead of the question, which became a
    list of one choice before it), the item moves to just before the first of them. Its own items are the ones drawn
    below it in its column, of another kind of label, before the next item of its own kind. Returns the order and how
    many items moved."""
    order = list(order)
    items = [e for e in order if isinstance(el := page.element(e), TextBlock) and el.role == "LI" and el.list_label]
    kinds = {e: label_kind(page.element(e).list_label)[0] for e in items}
    moved = 0
    for xid in items:
        x = page.element(xid)

        def column(b) -> bool:
            overlap = min(x.bbox[2], b.bbox[2]) - max(x.bbox[0], b.bbox[0])
            return overlap >= 0.5 * min(x.bbox[2] - x.bbox[0], b.bbox[2] - b.bbox[0]) > 0

        below = [e for e in items if e != xid and page.element(e).bbox[1] >= x.bbox[3] - 2 and column(page.element(e))]
        nxt = min((page.element(e).bbox[1] for e in below if kinds[e] == kinds[xid]), default=float("inf"))
        own = {e for e in below if kinds[e] != kinds[xid] and page.element(e).bbox[1] < nxt}
        at = order.index(xid)
        first = next((i for i, eid in enumerate(order[:at]) if eid in own), None)
        if first is not None:
            order.insert(first, order.pop(at))
            moved += 1
    return order, moved


async def _reading_order(ai, doc, analysis, stats) -> None:
    pages = [p for p in analysis.pages if needs_ai_reading_order(p) and len(p.reading_order) > 1]
    limit = get_settings().ai_order_pages_max
    if len(pages) > limit:
        stats.limit_hit("AI_ORDER_PAGES_MAX", limit, len(pages), "pages with columns kept the layout rules' reading order")
        stats.findings.append(f"{len(pages) - limit} page(s) with columns beyond the limit of {limit} "
                              f"(AI_ORDER_PAGES_MAX) kept the layout rules' reading order (from page "
                              f"{pages[limit].number + 1} on)")
        pages = pages[:limit]

    async def one(p: PageAnalysis) -> None:
        if len(p.reading_order) > MAX_ORDER_REGIONS:
            stats.limit_hit("MAX_ORDER_REGIONS", MAX_ORDER_REGIONS, 1, "pages had too many regions for an AI reading order "
                            "and kept the layout rules'", count=True)
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
        order, moved = headings_first(p, order)
        if moved:
            stats.findings.append(f"page {p.number + 1}: the AI read {moved} heading(s) after text drawn below them in "
                                  "their column; each now comes first")
        order, moved = items_before_choices(p, order)
        if moved:
            stats.findings.append(f"page {p.number + 1}: the AI read {moved} list item(s) after the items under them; "
                                  "each now comes first")
        if order != p.reading_order:
            stats.findings.append(f"page {p.number + 1}: reading order adjusted by AI")
        p.reading_order = order
        p.reading_order_source = "ai"
        stats.reading_order_pages += 1

    await asyncio.gather(*(one(p) for p in pages))
