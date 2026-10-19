"""Verification stage entry point: integrity -> semantics -> (optional) AI review."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

from app.config import get_settings
from app.verification.ai_review import FigureItem, OrderItem, ReviewPlan, review
from app.verification import contrast
from app.verification.integrity import check_integrity
from app.verification.model import Finding, VerificationReport
from app.verification.semantic import SemanticChecker
from app.tagging.metadata import document_info
from app.verification.tagmap import annotation_places, artifact_sections, page_sizes, tagmap_from


@dataclass
class VerifyOptions:
    ai: bool = True  # run Layer 3 when a judge is configured
    full_review: bool = False  # review every figure/page instead of flagged + sample
    seed: str = "0"  # makes the sample reproducible (the job id)
    kinds: list = field(default_factory=list)  # [(page 0-based, bbox, kind)] from the alt-text pass
    # Review only these elements' figures, every one of them (tag edits re-check the alt text people wrote)
    only_elements: set[int] | None = None
    tagmap: bool = True  # also build the tag map for the viewer
    judge_model: str | None = None  # the AI tier's review model at the gateway; None = VERIFY_JUDGE_*
    # Read-aloud text the AI wrote for specialist notation: [(page 0-based, bbox, kind, spoken[, two readings])].
    # Each is flagged for a person to check, and Formula ones go to the AI review. With two readings (#403: the
    # AI's and MathCAT's, which disagree), the finding carries both for Edit tag to offer.
    ai_read: list = field(default_factory=list)
    # Verdicts shared between the outputs of one job (pass the same dict for each output)
    review_cache: dict | None = None


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


_TEXT_ROLES = ("Formula", "P", "LBody", "LI", "TD", "TH", "Caption", "Lbl", "Span")


def _ai_read_findings(index, checker: SemanticChecker, items: list) -> list[Finding]:
    """One review finding per block whose read-aloud text the AI wrote, on the element that holds it."""
    out = []
    for item in items:
        page, bbox, kind, spoken = item[:4]
        readings = item[4] if len(item) > 4 else None
        roles = _TEXT_ROLES + ("Figure",) if readings else _TEXT_ROLES  # a formula drawn as a picture
        best, best_overlap = None, 0.0
        for e in index.elements:
            if e.role not in roles or checker._page_of(e) != page:
                continue
            c = checker.subtree(e)
            if c.bbox is None:
                continue
            ov = _overlap(bbox, c.bbox)
            area = max(1.0, (c.bbox[2] - c.bbox[0]) * (c.bbox[3] - c.bbox[1]))
            # the element mostly inside the block, preferring the one covering most of it
            if ov >= 0.5 * area and ov > best_overlap:
                best, best_overlap = e, ov
        snippet = str(spoken)[:160]
        if readings:
            # The readings that say other numbers or letters than the AI's: by rule from its LaTeX, from the picture
            other = {r["source"]: str(r["text"])[:160] for r in readings["readings"]}
            pairs = (("rule", "differs", "read by rule from its LaTeX"),
                     ("picture", "differs_picture", "read from the formula's picture"))
            said = [f"{how} it's \u201c{other[src]}\u201d" for src, key, how in pairs
                    if readings.get(key) and src in other]
            out.append(Finding(
                layer="ai", severity="review", issue="two readings of a formula disagree",
                page=page + 1, role=best.role if best else None, element=best.index if best else None,
                text=snippet,
                detail=f"the AI reads it as \u201c{snippet}\u201d; " + "; ".join(said)
                       + (", which say" if len(said) > 1 else ", which says") + " other numbers or letters",
                action="Choose the reading that matches what's printed in Edit tag, or write your own",
                extra=readings))
            continue
        if kind == "ocr-correct":
            issue, detail = "OCR text corrected by AI", f"words OCR was unsure of, corrected by AI: \u201c{snippet}\u201d"
        elif kind == "ocr-transcribe":
            issue, detail = "OCR text transcribed by AI", f"a line OCR couldn't read, transcribed by AI: \u201c{snippet}\u201d"
        else:
            issue = "read-aloud text written by AI"
            detail = f"{kind} notation the layout rules couldn't read; the AI reads it as \u201c{snippet}\u201d"
        out.append(Finding(
            layer="ai", severity="review", issue=issue,
            page=page + 1, role=best.role if best else None, element=best.index if best else None,
            detail=detail, action="Check it matches what's printed; correct it in the tag manager if not"))
    return out


def _rtl_findings(path: Path, max_pages: int) -> list[Finding]:
    """Pages mostly in Arabic or Hebrew, read right to left (#368): one finding naming them, to check the order."""
    import pymupdf as fitz

    from app.pipeline.structure import right_to_left

    with fitz.open(path) as doc:
        pages = [n + 1 for n, page in enumerate(doc) if n < max_pages and right_to_left(page.get_text())]
    if not pages:
        return []
    named = ", ".join(map(str, pages[:20])) + (f" and {len(pages) - 20} more" if len(pages) > 20 else "")
    return [Finding(
        layer="semantic", severity="review", issue="right-to-left reading order", page=pages[0],
        detail=f"{len(pages)} page(s) mostly in a right-to-left script (Arabic, Hebrew): columns and items side by "
               f"side are read from the right. Pages {named}",
        action="Check the reading order on these pages in the tag manager (Reading order view)",
        extra={"pages": pages})]


def _structural(path: Path, opts: VerifyOptions) -> tuple[VerificationReport, ReviewPlan]:
    settings = get_settings()
    report = VerificationReport()
    plan = ReviewPlan()
    with pikepdf.open(path) as pdf:
        findings, index = check_integrity(pdf)
        report.integrity_checks = 7
        report.elements_checked = len(index.elements)
        report.findings += findings
        report.integrity_passed = not report.errors
        if not report.integrity_passed:
            return report, plan
        checker = SemanticChecker(path, pdf, index, settings.verify_max_pages)
        report.findings += checker.run()
        report.skipped += checker.skipped
        report.findings += _ai_read_findings(index, checker, opts.ai_read)
        if settings.verify_contrast:
            report.findings += contrast.check(path, settings.verify_max_pages)
        report.findings += _rtl_findings(path, settings.verify_max_pages)
        if opts.tagmap:
            report.tagmap = tagmap_from(page_sizes(path), index, checker, artifact_sections(path),
                                        annotation_places(path), document_info(pdf))

        # Build the AI review plan while the PDF is open (alt text lives on the elements).
        flagged_elems = {f.element for f in report.findings if f.element is not None}
        flagged_pages = {f.page for f in report.findings if f.issue.startswith("reading order")}
        for e in index.elements:
            if e.role not in ("Figure", "Formula"):
                continue
            alt = str(e.obj.get("/Alt", "") or "").strip()
            c = checker.subtree(e)
            page = checker._page_of(e)
            if not alt or page is None or c.bbox is None or not (c.images or c.graphics or c.chars):
                continue
            kind = "formula" if e.role == "Formula" else "image"
            for kp, kb, kk in opts.kinds:
                if kp == page and _overlap(kb, c.bbox) > 0.5 * max(1.0, (c.bbox[2] - c.bbox[0]) * (c.bbox[3] - c.bbox[1])):
                    kind = kk
                    break
            plan.figures.append(FigureItem(element=e.index, role=e.role, page=page, bbox=c.bbox, alt=alt,
                                           context=_context(checker, e), kind=kind,
                                           flagged=e.index in flagged_elems))
        per_page: dict[int, list] = {}
        for e in index.elements:
            c = checker.own.get(e.index)
            if c is not None and e.page is not None and c.bbox and (c.text or c.images):
                per_page.setdefault(e.page, []).append((c.bbox, e.index))
        for page, boxes in per_page.items() if settings.verify_ai_reading_order else []:
            if 4 <= len(boxes) <= 60:
                plan.pages.append(OrderItem(page=page, boxes=boxes, flagged=(page + 1) in flagged_pages))
    return report, plan


def _context(checker: SemanticChecker, e) -> str:
    if e.parent is None:
        return ""
    els = checker.index.elements
    sibs = els[e.parent].kids
    pos = sibs.index(e.index) if e.index in sibs else 0
    parts = [checker.subtree(els[k]).text for k in sibs[max(0, pos - 2): pos + 3] if k != e.index]
    return " ".join(p for p in parts if p)[:1200]


async def verify_output(path: Path, opts: VerifyOptions) -> VerificationReport:
    settings = get_settings()
    report, plan = await asyncio.to_thread(_structural, path, opts)
    if not report.integrity_passed:
        return report
    full = opts.full_review
    if opts.only_elements is not None:
        plan.figures = [f for f in plan.figures if f.element in opts.only_elements]
        plan.pages = []
        full = True
    if opts.ai and settings.verify_ai_enabled and (settings.verify_judge_provider != "none" or opts.judge_model) and (
            opts.only_elements is None or plan.figures):
        ai_findings, meta = await review(path, plan, full, opts.seed, opts.judge_model, cache=opts.review_cache)
        report.findings += ai_findings
        report.ai = meta
    else:
        report.ai = {"mode": "off"}
    return report
