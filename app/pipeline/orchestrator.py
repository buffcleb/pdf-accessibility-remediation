"""End-to-end remediation flow for one job.

  1. Validate the ORIGINAL (veraPDF + internal audit)        -> "before" report
  2. Analyze layout (PyMuPDF); OCR scanned pages (invisible text layer)
  3. AI enrichment: alt text, table structure, reading order
  4. Non-destructive tagging (existing valid tags retained)  -> visual output
  5. Validate. If failures remain that only destructive fixes can address, flatten the
     affected pages and tag again                            -> modified output
  6. Validate outputs                                        -> "after" report, comparison, summary
"""
from __future__ import annotations

import asyncio
import re
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import pymupdf as fitz

from app.ai.base import AIError
from app.ai.registry import get_provider
from app.config import get_settings
from app.pipeline.analyzer import analyze_document
from app.pipeline.enrichment import EnrichmentStats, enrich
from app.pipeline.models import DocumentAnalysis
from app.pipeline.ocr import ocr_lines, tesseract_available, tesseract_cli, write_ocr_layer
from app.pipeline.ocr_ai import correct_lines
from app.pipeline import scan_figures
from app.pipeline.structure import assign_roles, detect_columns, heuristic_reading_order
from app.pipeline.transfer import transfer_enrichment
from app.storage import JobStore
from app.tagging.destructive import DESTRUCTIVE_CLAUSES, flatten_pages
from app.tagging.font_embed import embed_standard_fonts
from app.tagging.font_unicode import add_missing_tounicode
from app.tagging.font_metrics import fix_width_mismatches
from app.tagging.tagger import TagResult, tag_document
from app.validation.audit import audit_pdf
from app.validation.verapdf import VeraResult, run_verapdf
from app.verification.model import IntegrityError
from app.verification.tagmap import build_tagmap
from app.verification.verify import VerifyOptions, verify_output

log = logging.getLogger(__name__)

Progress = Callable[[str, int], None]


class AIProviderUnavailable(RuntimeError):
    """The requested AI provider cannot be reached or lacks the model: fail the job up front."""


@dataclass
class JobOptions:
    stem: str
    provider: str = "none"
    model: str | None = None
    debug: bool = False
    report_only: bool = False
    return_base64: bool = False
    language: str | None = None
    title: str | None = None
    full_review: bool = False
    verify_ai: bool = True
    job_id: str = "0"
    ai_tier: str | None = None  # the portal's AI tier, for attributing AI spend
    judge_model: str | None = None  # the tier's review model at the gateway
    route: str = "standard"  # standard | adobe
    notation_ai: bool = True  # the AI reads specialist notation the layout rules can't
    ocr_ai: bool = True  # the AI corrects OCR words Tesseract was unsure of

    @classmethod
    def from_meta(cls, meta: dict) -> "JobOptions":
        flags = meta.get("flags") or {}
        return cls(
            stem=meta.get("stem") or "document",
            provider=meta.get("ai_provider") or "none",
            model=meta.get("ai_model") or None,
            debug=bool(flags.get("debug")),
            report_only=bool(flags.get("report_only")),
            return_base64=bool(flags.get("return_base64")),
            language=flags.get("language"),
            title=flags.get("title"),
            full_review=bool(flags.get("full_review")),
            verify_ai=flags.get("verify_ai", True) is not False,
            job_id=meta.get("job_id") or "0",
            ai_tier=meta.get("ai_tier") or None,
            judge_model=meta.get("judge_model") or None,
            route=meta.get("route") or "standard",
            notation_ai=flags.get("notation_ai", True) is not False,
            ocr_ai=flags.get("ocr_ai", True) is not False,
        )


# --------------------------------------------------------------------------- helpers
def prepare_analysis(path: Path) -> DocumentAnalysis:
    analysis = analyze_document(path)
    assign_roles(analysis)
    for p in analysis.pages:
        p.columns = detect_columns(p)
        p.reading_order = heuristic_reading_order(p)
    return analysis


def _validate(pdf: Path, out_dir: Path, label: str, stem: str) -> dict[str, VeraResult]:
    results = {}
    for fl in get_settings().flavours:
        results[fl] = run_verapdf(pdf, out_dir / f"{stem}_verapdf_{label}_{fl}.xml", fl)
    return results


def _snapshot(audit: dict, vera: dict[str, VeraResult]) -> dict:
    primary = next(iter(vera.values()), None)
    snap = {
        "compliant": primary.compliant if primary and primary.available else None,
        "failed_rules": primary.failed_rules if primary else None,
        "failed_checks": primary.failed_checks if primary else None,
        "verapdf": {
            fl: ({"compliant": v.compliant, "passed_rules": v.passed_rules, "failed_rules": v.failed_rules,
                  "failed_checks": v.failed_checks, "profile": v.profile,
                  "failed_clauses": sorted(v.failed_clauses),
                  "failures": [{"clause": f.clause, "test": f.test, "description": f.description,
                                "failed_checks": f.failed_checks, "pages": [p + 1 for p in f.pages][:20]}
                               for f in v.failures[:25]]} if v.available and not v.error
                 else {"error": v.error or "unavailable"})
            for fl, v in vera.items()
        },
    }
    snap.update(audit)
    return snap


def _visual_fidelity(a: Path, b: Path, dpi: int = 50) -> dict:
    da, db = fitz.open(a), fitz.open(b)
    try:
        differing = []
        for i in range(min(da.page_count, db.page_count)):
            if da[i].get_pixmap(dpi=dpi, alpha=False).samples != db[i].get_pixmap(dpi=dpi, alpha=False).samples:
                differing.append(i + 1)
        return {"pages_compared": da.page_count, "pages_identical": da.page_count - len(differing),
                "differing_pages": differing[:50], "identical": not differing and da.page_count == db.page_count}
    finally:
        da.close()
        db.close()


def _pick_title(opts: JobOptions, analysis: DocumentAnalysis) -> str:
    if opts.title:
        return opts.title
    if analysis.title and analysis.title.lower() not in ("untitled", "document", "microsoft word"):
        return analysis.title
    for p in analysis.pages:
        for b in p.blocks:
            if b.role == "H1":
                return b.text[:200]
    return opts.stem.replace("_", " ")


def _primary(v: dict[str, VeraResult]) -> VeraResult | None:
    r = next(iter(v.values()), None)
    return r if r and r.available and not r.error else None


# --------------------------------------------------------------------------- main
async def run_remediation(store: JobStore, opts: JobOptions, progress: Progress = lambda s, p: None) -> dict:
    settings = get_settings()
    started = time.monotonic()
    work, out, stem = store.work_dir, store.output_dir, opts.stem
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    src = store.input_pdf
    warnings: list[str] = []

    # 1. before -------------------------------------------------------------------------
    progress("validating_original", 5)
    before_audit = await asyncio.to_thread(audit_pdf, src)
    before_vera = await asyncio.to_thread(_validate, src, out, "before", stem)
    if not _primary(before_vera):
        warnings.append(f"veraPDF unavailable: {next(iter(before_vera.values())).error}")

    # 2. analysis + OCR -------------------------------------------------------------------
    progress("analyzing", 15)
    # Fill missing Unicode maps first (rendering unchanged), so analysis and the AI read real text.
    base = src
    unicode_maps = await asyncio.to_thread(add_missing_tounicode, src, work / "tounicode.pdf")
    if unicode_maps:
        base = work / "tounicode.pdf"
    analysis = await asyncio.to_thread(prepare_analysis, base)
    scanned = [p.number for p in analysis.pages if p.is_scanned]
    enrichment = EnrichmentStats()
    ai = get_provider(opts.provider, opts.model, log_path=(work / "ai_log.jsonl") if opts.debug else None)
    if ai is not None:
        ai.tags = {"job": opts.job_id, **({"tier": opts.ai_tier} if opts.ai_tier else {})}
    ocr_count = 0
    scan_figure_count = 0
    if scanned:
        if settings.ocr_enabled and tesseract_available():
            ocr_path = work / "ocr.pdf"
            # The AI corrects the words Tesseract was unsure of before the text layer is written
            ocr_ai = (ai is not None and opts.ocr_ai and settings.ocr_ai_enabled and settings.ocr_engine != "pymupdf"
                      and tesseract_cli() is not None)
            pictures: dict[int, list] = {}  # what OCR read as letters but looks like a picture
            ocr_records = await asyncio.to_thread(ocr_lines, str(base), scanned, True, pictures)
            if ocr_ai:
                progress("ai_ocr", 22)
                async with ai:
                    try:
                        await ai.preflight()
                    except AIError as exc:
                        raise AIProviderUnavailable(f"AI provider '{opts.provider}' is not usable: {exc}") from exc
                    await correct_lines(ai, str(base), ocr_records, enrichment)
            ocr_count = await asyncio.to_thread(write_ocr_layer, str(base), str(ocr_path), ocr_records)
            base = ocr_path
            # Figures printed on the scan (charts, photos) are drawn again on their own, clipped, so they can be
            # tagged and described while the rest of the scan is background. The pages look exactly the same.
            text_boxes: dict[int, list] = {}
            for rec in ocr_records:
                text_boxes.setdefault(rec.page, []).append(rec.bbox)
            figures_path = work / "scan_figures.pdf"
            try:
                scan_regions = await asyncio.to_thread(scan_figures.separate, str(base), str(figures_path), scanned,
                                                       text_boxes, pictures)
            except Exception as exc:  # noqa: BLE001 - a problem here only means the figures stay untagged
                log.warning("figures on scanned pages not separated: %s", exc)
                scan_regions = {}
            if scan_regions:
                base = figures_path
            analysis = await asyncio.to_thread(prepare_analysis, base)
            # The OCR text now carries the content; the page scan itself becomes a background artifact.
            for p in analysis.pages:
                if p.number in scanned:
                    for img in p.images:
                        if img.bbox[2] - img.bbox[0] >= 0.9 * p.width and img.bbox[3] - img.bbox[1] >= 0.9 * p.height:
                            img.decorative = True
                    if scan_regions.get(p.number):
                        scan_figure_count += scan_figures.add_to_page(p, scan_regions[p.number])
                        p.reading_order = heuristic_reading_order(p)
                    p.reading_order = [e for e in p.reading_order if not (
                        (el := p.element(e)) is not None and getattr(el, "decorative", False))]
        else:
            warnings.append(f"{len(scanned)} scanned page(s) without a text layer; OCR is unavailable")

    # 3. AI ---------------------------------------------------------------------------------
    progress("ai_enrichment", 30)
    if ai is not None:
        async with ai:
            try:
                await ai.preflight()
            except AIError as exc:
                raise AIProviderUnavailable(f"AI provider '{opts.provider}' is not usable: {exc}") from exc
            enrichment = await enrich(ai, base, analysis, dpi=min(settings.raster_dpi, 150), notation=opts.notation_ai,
                                      stats=enrichment)
        if ai.failures and not ai.calls:
            warnings.append(f"every AI call failed ({ai.failures}); alt text, tables and reading order fell back to "
                            "heuristics. See ai_findings for the error")
        elif ai.failures:
            warnings.append(f"{ai.failures} of {ai.calls + ai.failures} AI calls failed; see ai_findings")
    else:
        if any(not i.decorative for p in analysis.pages for i in p.images):
            warnings.append("ai_provider 'none': figures are tagged without alt text")
    if opts.debug:
        (work / "analysis.json").write_text(json.dumps(analysis.to_dict(), indent=1, default=str))

    metrics = {
        "pages_processed": analysis.page_count,
        "ai_tokens_used": {"prompt_tokens": ai.usage.prompt_tokens if ai else 0,
                           "completion_tokens": ai.usage.completion_tokens if ai else 0},
        "ai_cost_usd": round(ai.usage.cost_usd, 6) if ai else 0.0,
        "ai_calls": ai.calls if ai else 0,
        "ai_failures": ai.failures if ai else 0,
        "ai_provider": opts.provider,
        "ai_model": ai.model if ai else None,
        "images_described": enrichment.images_described,
        "notation_read_by_ai": len(enrichment.notation),
        "notation_ai_rejected": enrichment.notation_rejected,
        "tables_analyzed": enrichment.tables_analyzed,
        "reading_order_pages_ai": enrichment.reading_order_pages,
        "ocr_pages": len(scanned) if ocr_count else 0,
        "scan_figures": scan_figure_count,
        "ocr_ai_words_corrected": sum(len(o["changes"]) for o in enrichment.ocr if o["mode"] == "correct"),
        "ocr_ai_lines_transcribed": sum(1 for o in enrichment.ocr if o["mode"] == "transcribe"),
        "ocr_ai_rejected": enrichment.ocr_rejected,
    }
    before_snapshot = _snapshot(before_audit, before_vera)

    if opts.report_only:
        summary = _report_only_summary(analysis, before_snapshot, enrichment)
        verification = None
        if settings.verify_enabled and analysis_has_tags(src):
            progress("verifying_tags", 80)
            rep = await verify_output(src, VerifyOptions(ai=opts.verify_ai, full_review=opts.full_review,
                                                         seed=opts.job_id, kinds=_kinds(analysis),
                                                         judge_model=opts.judge_model))
            verification = {"original": rep.to_dict()}
        tag_maps = {"original": await _tagmap_file(out, stem, "original", src)}
        metrics["processing_time_seconds"] = round(time.monotonic() - started, 2)
        result = {
            "files": {"remediated_primary": None, "remediated_visual_fallback": None},
            "metrics": metrics,
            "reports": {
                "verapdf_before": _xml_name(before_vera),
                "verapdf_after": None,
                "comparison": {"before": before_snapshot, "after": None,
                               "ai_audit": _ai_audit(analysis, enrichment)},
                "remediation_summary": summary,
                "ai_findings": enrichment.findings[:100],
                "tag_verification": verification,
                "tag_maps": tag_maps,
            },
            "warnings": warnings,
        }
        _write_json_report(out, stem, result, store)
        return result

    # 4. visual (non-destructive) ----------------------------------------------------------
    progress("tagging", 55)
    title = _pick_title(opts, analysis)
    language = opts.language or analysis.language or settings.default_language
    visual = work / "visual.pdf"
    visual_region_alts: list = []
    tag_visual = await asyncio.to_thread(
        tag_document, base, visual, analysis, title=title, language=language,
        pdfua_part=settings.pdfua_part, layout_tables=enrichment.layout_tables)
    if tag_visual.figures_needing_alt and ai is not None:
        # Existing Figure tags over vector art or equations: describe the rendered region, re-tag.
        progress("ai_existing_figures", 62)
        async with ai:
            region_alts = await _describe_regions(ai, base, tag_visual.figures_needing_alt, enrichment)
        metrics["ai_tokens_used"] = {"prompt_tokens": ai.usage.prompt_tokens,
                                     "completion_tokens": ai.usage.completion_tokens}
        metrics["ai_cost_usd"] = round(ai.usage.cost_usd, 6)
        metrics["ai_calls"] = ai.calls
        metrics["ai_failures"] = ai.failures
        if region_alts:
            tag_visual = await asyncio.to_thread(
                tag_document, base, visual, analysis, title=title, language=language,
                pdfua_part=settings.pdfua_part, layout_tables=enrichment.layout_tables, region_alts=region_alts)
            visual_region_alts = region_alts
    progress("validating_output", 70)
    vera_visual = await asyncio.to_thread(_validate, visual, work, "after_visual", stem)
    # Width corrections are exact on paper, but many of them can shift glyphs by a fraction of a
    # point in real renderers. Keep them in the visual file only if the pages stay pixel-identical;
    # otherwise move them to the modified file.
    widths_subpixel: tuple | None = None  # (path, vera, count) of a width-fixed copy for the modified file
    # Incomplete glyph lists in font descriptors (7.21.4.2): removing them never changes rendering.
    vera_visual, glyph_lists_fixed = await asyncio.to_thread(_fix_glyph_lists, visual, vera_visual, work,
                                                             "after_visual", stem)
    pre_fix = work / "visual_before_widthfix.pdf"
    shutil.copyfile(visual, pre_fix)
    vera_prefix = vera_visual
    vera_visual, widths_fixed = await asyncio.to_thread(_fix_widths, visual, vera_visual, work, "after_visual", stem)
    if widths_fixed and not (await asyncio.to_thread(_visual_fidelity, src, visual))["identical"]:
        wf = work / "visual_widthfixed.pdf"
        shutil.move(visual, wf)
        shutil.copyfile(pre_fix, visual)
        widths_subpixel = (wf, vera_visual, widths_fixed)
        vera_visual, widths_fixed = vera_prefix, 0
        warnings.append("glyph width corrections would move text by a fraction of a point, so they were applied "
                        "to the modified file only; the visually exact file keeps the original widths")

    # 5. modified (destructive) if required ---------------------------------------------
    # Least destructive first: embed metric-compatible substitutes for unembedded standard fonts
    # (tags untouched); then flatten only the pages that still fail.
    modified: Path | None = None
    tag_modified: TagResult | None = None
    vera_modified: dict[str, VeraResult] | None = None
    destructive_pages: list[int] = []
    fonts_embedded: list[str] = []
    pv = _primary(vera_visual)
    needs_fix = widths_subpixel is not None or not (pv and pv.compliant) and (
        (pv is not None and bool(pv.failed_clauses_with(DESTRUCTIVE_CLAUSES)))
        or (pv is None and any(p.font_issues for p in analysis.pages)))
    if needs_fix:
        progress("destructive_fixes", 78)
        stage_src = base
        emb = work / "fonts_embedded.pdf"
        fonts_embedded = await asyncio.to_thread(embed_standard_fonts, base, emb)
        candidate = work / "modified.pdf"
        fonts_only: tuple | None = None  # fallback if flattening is later rejected
        if widths_subpixel is not None and not fonts_embedded:
            # Least destructive candidate: the visual file with the width corrections.
            candidate, vera_modified = widths_subpixel[0], widths_subpixel[1]
            tag_modified = tag_visual
            fonts_only = (candidate, tag_modified, vera_modified)
        if fonts_embedded:
            stage_src = emb
            fonts_candidate = work / "modified_fonts.pdf"
            tag_modified = await asyncio.to_thread(
                tag_document, stage_src, fonts_candidate, analysis, title=title, language=language,
                pdfua_part=settings.pdfua_part, layout_tables=enrichment.layout_tables, region_alts=visual_region_alts)
            vera_modified = await asyncio.to_thread(_validate, fonts_candidate, work, "after_modified_fonts", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_glyph_lists, fonts_candidate, vera_modified, work,
                                                       "after_modified_fonts", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_widths, fonts_candidate, vera_modified, work,
                                                       "after_modified_fonts", stem)
            candidate = fonts_candidate
            fonts_only = (fonts_candidate, tag_modified, vera_modified)
        if widths_subpixel is not None and not fonts_embedded:
            stage_src = base  # flattening re-tags from the untagged base; widths are re-fixed afterwards
        pm = _primary(vera_modified) if vera_modified else pv
        if not (pm and pm.compliant):
            destructive_pages = await asyncio.to_thread(_destructive_candidates, pm, analysis, stage_src)
        if destructive_pages:
            progress("flattening", 84)
            flat = work / "flattened.pdf"
            candidate = work / "modified.pdf"
            vectors = {p.number: [i.bbox for i in p.images if i.kind == "vector" or (i.xref == 0 and not i.decorative)]
                       for p in analysis.pages if p.number in destructive_pages}
            await asyncio.to_thread(flatten_pages, str(stage_src), str(flat), destructive_pages, vectors)
            analysis_mod = await asyncio.to_thread(prepare_analysis, flat)
            layout_mod = transfer_enrichment(analysis, analysis_mod, set(destructive_pages), enrichment.layout_tables,
                                             notation=enrichment.notation)
            tag_modified = await asyncio.to_thread(
                tag_document, flat, candidate, analysis_mod, title=title, language=language,
                pdfua_part=settings.pdfua_part, layout_tables=layout_mod, region_alts=visual_region_alts)
            vera_modified = await asyncio.to_thread(_validate, candidate, work, "after_modified", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_glyph_lists, candidate, vera_modified, work,
                                                       "after_modified", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_widths, candidate, vera_modified, work, "after_modified", stem)
        if vera_modified is not None:
            verdict = await asyncio.to_thread(_judge_candidate, pv, vera_modified, visual, candidate)
            if verdict and destructive_pages and fonts_only is not None:
                # Flattening cost too much structure: fall back to the fonts-only version.
                warnings.append(f"page flattening was not applied: {verdict}")
                candidate, tag_modified, vera_modified = fonts_only
                destructive_pages = []
                verdict = await asyncio.to_thread(_judge_candidate, pv, vera_modified, visual, candidate)
            if verdict:
                warnings.append(f"the modified version was discarded: {verdict}; only the visually exact output is returned")
                modified, tag_modified, vera_modified, destructive_pages, fonts_embedded = None, None, None, [], []
            else:
                modified = candidate

    # 6. outputs + after reports ---------------------------------------------------------
    progress("reporting", 90)
    files: dict[str, str | None] = {"remediated_primary": None, "remediated_visual_fallback": None}
    vera_after: dict[str, str | None] = {"primary": None, "visual_fallback": None}
    if modified is not None:
        primary_name, fallback_name = f"{stem}_compliant_modified.pdf", f"{stem}_compliant_visual.pdf"
        shutil.copyfile(modified, out / primary_name)
        shutil.copyfile(visual, out / fallback_name)
        files = {"remediated_primary": primary_name, "remediated_visual_fallback": fallback_name}
        vera_after["primary"] = _move_xml(vera_modified, out, f"{stem}_verapdf_after")
        vera_after["visual_fallback"] = _move_xml(vera_visual, out, f"{stem}_verapdf_after_visual")
        primary_vera, primary_path = vera_modified, out / primary_name
    else:
        primary_name = f"{stem}_remediated.pdf"
        shutil.copyfile(visual, out / primary_name)
        files["remediated_primary"] = primary_name
        vera_after["primary"] = _move_xml(vera_visual, out, f"{stem}_verapdf_after")
        primary_vera, primary_path = vera_visual, out / primary_name

    verification = None
    # Tag maps for the portal's viewer: the original and every output
    tag_maps = {"original": await _tagmap_file(out, stem, "original", src)}
    if settings.verify_enabled:
        progress("verifying_tags", 92)
        verification = {}
        vopts = VerifyOptions(ai=opts.verify_ai, full_review=opts.full_review, seed=opts.job_id,
                              kinds=_kinds(analysis), judge_model=opts.judge_model,
                              ai_read=[(n["page"], n["bbox"], n["kind"], n["spoken"]) for n in enrichment.notation]
                              + [(o["page"], o["bbox"], f"ocr-{o['mode']}", o["after"]) for o in enrichment.ocr],
                              review_cache={})  # one verdict per figure across both output files
        outputs = [("primary", out / files["remediated_primary"])]
        if files.get("remediated_visual_fallback"):
            outputs.append(("visual_fallback", out / files["remediated_visual_fallback"]))
        for label, path in outputs:
            rep = await verify_output(path, vopts)
            if not rep.integrity_passed:
                raise IntegrityError(label, rep.errors)
            verification[label] = rep.to_dict()
            tag_maps[label] = await _tagmap_file(out, stem, label, path, rep.tagmap)
            if rep.ai.get("error") and rep.ai["error"] not in " ".join(warnings):
                warnings.append(f"AI review did not run: {rep.ai['error']}")
            if rep.ai.get("ai_tokens_used"):
                tok = metrics.setdefault("verification_ai_tokens", {"prompt_tokens": 0, "completion_tokens": 0})
                for k in tok:
                    tok[k] += rep.ai["ai_tokens_used"].get(k, 0)
                metrics["verification_ai_cost_usd"] = round(metrics.get("verification_ai_cost_usd", 0.0)
                                                            + float(rep.ai["ai_tokens_used"].get("cost_usd", 0.0)), 6)

    for label, key in (("primary", "remediated_primary"), ("visual_fallback", "remediated_visual_fallback")):
        if files.get(key) and label not in tag_maps:
            tag_maps[label] = await _tagmap_file(out, stem, label, out / files[key])

    after_audit = await asyncio.to_thread(audit_pdf, primary_path)
    after_snapshot = _snapshot(after_audit, primary_vera)
    comparison = {
        "before": before_snapshot,
        "after": after_snapshot,
        "delta": _delta(before_snapshot, after_snapshot),
        "existing_tags": tag_visual.existing_tags,
        "visual_fidelity": await asyncio.to_thread(_visual_fidelity, src, visual),
        "glyph_widths_compensated": widths_fixed,
        "glyph_widths_modified_only": widths_subpixel[2] if widths_subpixel else 0,
        "glyph_lists_removed": glyph_lists_fixed,
        "unicode_maps_added": unicode_maps,
    }
    if modified is not None:
        comparison["after_visual_fallback"] = _snapshot(await asyncio.to_thread(audit_pdf, visual), vera_visual)
        comparison["destructive_pages"] = [p + 1 for p in destructive_pages]
        comparison["fonts_embedded"] = fonts_embedded

    metrics["processing_time_seconds"] = round(time.monotonic() - started, 2)
    summary = _summary(analysis, tag_visual, tag_modified, enrichment, before_snapshot, after_snapshot,
                       comparison, destructive_pages, warnings, title, language)
    result = {
        "files": files,
        "metrics": metrics,
        "reports": {
            "verapdf_before": _xml_name(before_vera),
            "verapdf_after": vera_after if any(vera_after.values()) else None,
            "comparison": comparison,
            "remediation_summary": summary,
            "tagging": {"visual": asdict(tag_visual), "modified": asdict(tag_modified) if tag_modified else None},
            "tag_verification": verification,
            "tag_maps": tag_maps,
            "ai_findings": enrichment.findings[:100],
            # Specialist notation the AI read (page 1-based, region, kind, read-aloud text, LaTeX, confidence)
            "ai_notation": [{**n, "page": n["page"] + 1} for n in enrichment.notation][:300] or None,
            # OCR lines the AI corrected or transcribed (page 1-based, region, mode, before, after, changed words)
            "ai_ocr": [{**o, "page": o["page"] + 1} for o in enrichment.ocr][:500] or None,
        },
        "warnings": warnings,
    }
    _write_json_report(out, stem, result, store)
    progress("completed", 100)
    return result


def _kinds(analysis: DocumentAnalysis) -> list:
    """Content types from the alt-text pass, used to route figures to the right judge."""
    return [(p.number, i.bbox, i.kind) for p in analysis.pages for i in p.images if i.kind]


def analysis_has_tags(pdf: Path) -> bool:
    import pikepdf

    with pikepdf.open(pdf) as doc:
        return "/StructTreeRoot" in doc.Root


MAX_REGION_FIGURES = 150


async def _describe_regions(ai, pdf_path: Path, regions: list, enrichment: EnrichmentStats) -> list:
    from app.ai.base import AIError
    from app.pipeline.analyzer import render_png

    doc = fitz.open(pdf_path)
    try:
        async def one(page: int, bbox) -> tuple | None:
            if (bbox[2] - bbox[0]) < 6 and (bbox[3] - bbox[1]) < 6:
                # A few-point dot or mark tagged as a figure carries no information: make it an artifact.
                enrichment.findings.append(f"page {page + 1}: tiny figure ({bbox[2] - bbox[0]:.0f}×"
                                           f"{bbox[3] - bbox[1]:.0f} pt) marked as an artifact")
                return (page, tuple(bbox), None, True)
            png = render_png(doc, page, dpi=150, clip=bbox, max_px=1200)
            try:
                res = await ai.describe_image(png, page, "")
            except (AIError, ValueError) as exc:
                enrichment.findings.append(f"page {page + 1}: alt text for existing figure failed ({exc})")
                return None
            if res["decorative"] and not res["alt"]:
                enrichment.findings.append(f"page {page + 1}: existing figure judged decorative; "
                                           "its tag was removed and the content marked as an artifact (review)")
                return (page, tuple(bbox), None, True)
            if not res["alt"]:
                return None
            if res["decorative"]:
                # It is already a Figure tag, which must have alt text: keep the model's description.
                enrichment.findings.append(f"page {page + 1}: existing figure judged decorative; "
                                           "kept as a figure with a short description (review)")
            enrichment.images_described += 1
            return (page, tuple(bbox), res["alt"], False)

        results = await asyncio.gather(*(one(p, b) for p, b in regions[:MAX_REGION_FIGURES]))
    finally:
        doc.close()
    if len(regions) > MAX_REGION_FIGURES:
        enrichment.findings.append(f"{len(regions) - MAX_REGION_FIGURES} existing figure(s) beyond the limit left without alt")
    return [r for r in results if r]


# --------------------------------------------------------------------------- decisions / reporting
def _destructive_candidates(pv: VeraResult | None, analysis: DocumentAnalysis, pdf_path: Path) -> list[int]:
    """Pages to flatten: pages veraPDF attributes destructive-only failures to, plus every page that
    draws text with a failing font (veraPDF reports each font problem only on its first page)."""
    pages: set[int] = set()
    if pv is None:  # no validator: fall back to the analyser's font findings
        return sorted({p.number for p in analysis.pages if p.font_issues})
    pages |= pv.pages_failing(DESTRUCTIVE_CLAUSES)
    fonts = pv.failing_fonts(("7.21",))
    if fonts:
        doc = fitz.open(pdf_path)
        try:
            for i in range(doc.page_count):
                used = {f[3].split("+", 1)[-1] for f in doc[i].get_fonts(full=True)}
                if used & fonts and doc[i].get_text("text").strip():
                    pages.add(i)
        finally:
            doc.close()
    if not pages and pv.unattributed(("7.21",)):
        pages = {p.number for p in analysis.pages if p.font_issues}
    return sorted(p for p in pages if 0 <= p < analysis.page_count)


def _fix_glyph_lists(pdf: Path, vera: dict[str, VeraResult], work: Path, label: str, stem: str
                     ) -> tuple[dict[str, VeraResult], list[str]]:
    """Remove incomplete CIDSet/CharSet glyph lists that veraPDF reports (7.21.4.2), in place, and
    re-validate. Rendering is unchanged: nothing draws with these lists."""
    from app.tagging.font_descriptors import CLAUSE, remove_incomplete_glyph_lists

    pv = _primary(vera)
    if pv is None or CLAUSE not in pv.failed_clauses:
        return vera, []
    tmp = work / f"glyphlists_{pdf.name}"
    fixed = remove_incomplete_glyph_lists(pdf, tmp, [f for f in pv.failures if f.clause == CLAUSE])
    if not fixed:
        return vera, []
    tmp.replace(pdf)
    return _validate(pdf, work, label, stem), fixed


def _fix_widths(pdf: Path, vera: dict[str, VeraResult], work: Path, label: str, stem: str
                ) -> tuple[dict[str, VeraResult], int]:
    """Repair 7.21.5 width mismatches in place (pixel-identical), re-validating up to 3 rounds
    because veraPDF lists a limited number of failed checks per rule."""
    total = 0
    for _ in range(3):
        pv = _primary(vera)
        if pv is None or not pv.width_mismatches:
            break
        tmp = work / f"widthfix_{pdf.name}"
        n = fix_width_mismatches(pdf, tmp, pv.width_mismatches)
        if not n:
            break
        tmp.replace(pdf)
        total += n
        vera = _validate(pdf, work, label, stem)
    return vera, total


def _judge_candidate(pv: VeraResult | None, vera_candidate: dict, visual: Path, candidate: Path) -> str | None:
    """None if the modified candidate is worth returning, else the reason it is not."""
    pm = _primary(vera_candidate)
    better = (pm is not None and pv is not None and (pm.compliant or pm.failed_checks < pv.failed_checks)) or (
        pv is None and pm is None)
    if not better:
        return "it did not reduce validation failures"
    return _structure_loss(visual, candidate)


def _heading_chars(path: Path) -> int:
    """Amount of text tagged as headings (H, H1-H6), in non-space characters."""
    elements = build_tagmap(path)["elements"]
    return sum(len(re.sub(r"\s", "", e.get("text") or "")) for e in elements
               if re.fullmatch(r"H[1-6]?", e["role"]) and e.get("own"))


def _structure_loss(visual: Path, candidate: Path) -> str | None:
    """Reject a modified file that loses meaningful structure compared with the visual one."""
    a, b = audit_pdf(visual), audit_pdf(candidate)
    for key, label in (("figures", "figures"), ("tables", "tables"), ("headings", "headings"),
                       ("struct_elements", "structure elements")):
        if a.get(key, 0) >= 3 and b.get(key, 0) < 0.8 * a[key]:
            # Fewer heading tags is fine when the same heading text is still tagged as headings: a
            # flattened page can read a two-line title as one heading.
            if key == "headings" and _heading_chars(candidate) >= 0.9 * _heading_chars(visual) > 0:
                continue
            return f"it would drop {label} from {a[key]} to {b.get(key, 0)}"
    return None


def _xml_name(vera: dict[str, VeraResult]) -> str | None:
    r = next(iter(vera.values()), None)
    return Path(r.xml_path).name if r and r.xml_path else None


def _move_xml(vera: dict[str, VeraResult] | None, out: Path, prefix: str) -> str | None:
    if not vera:
        return None
    primary_name = None
    for i, (fl, r) in enumerate(vera.items()):
        if r.xml_path and Path(r.xml_path).exists():
            name = f"{prefix}_{fl}.xml"
            shutil.copyfile(r.xml_path, out / name)
            if i == 0:
                primary_name = name
    return primary_name


def _delta(before: dict, after: dict) -> dict:
    keys = ["failed_rules", "failed_checks", "struct_elements", "figures_missing_alt", "tables_without_headers",
            "heading_level_skips", "content_tagged_ratio", "annotations_untagged", "font_problems"]
    out = {}
    for k in keys:
        b, a = before.get(k), after.get(k)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            out[k] = {"before": b, "after": a, "change": round(a - b, 4)}
    return out


def _ai_audit(analysis: DocumentAnalysis, enrichment: EnrichmentStats) -> dict:
    return {
        "figures": [{"page": i.page + 1, "bbox": i.bbox, "decorative": i.decorative, "suggested_alt": i.alt,
                     "kind": i.kind} for p in analysis.pages for i in p.images][:200],
        "tables": [{"page": t.page + 1, "rows": t.n_rows, "cols": t.n_cols, "header_rows": t.header_rows,
                    "header_cols": t.header_cols, "layout_table": t.id in enrichment.layout_tables,
                    "summary": t.summary} for p in analysis.pages for t in p.tables][:100],
        "multi_column_pages": [p.number + 1 for p in analysis.pages if p.columns > 1],
        "notation": [{**n, "page": n["page"] + 1} for n in enrichment.notation][:200],
        "findings": enrichment.findings[:100],
    }


def _report_only_summary(analysis, before: dict, enrichment: EnrichmentStats) -> str:
    lines = ["Report only: no changes were applied.", _verdict_line("Original", before)]
    lines.append(f"Tagged: {'yes' if before.get('tagged') else 'no'}; "
                 f"{before.get('content_tagged_ratio', 0):.0%} of content is tagged or marked as artifact.")
    figs = sum(1 for p in analysis.pages for i in p.images if not i.decorative)
    lines.append(f"Detected {figs} informative figure(s), {sum(len(p.tables) for p in analysis.pages)} table(s), "
                 f"{sum(1 for p in analysis.pages if p.columns > 1)} multi-column page(s).")
    if enrichment.images_described:
        lines.append(f"AI drafted alt text for {enrichment.images_described} figure(s) (see comparison.ai_audit).")
    return "\n".join(lines)


def _verdict_line(label: str, snap: dict) -> str:
    if snap.get("compliant") is None:
        return f"{label}: veraPDF verdict unavailable."
    status = "PASSES" if snap["compliant"] else "FAILS"
    return f"{label}: {status} PDF/UA validation ({snap['failed_rules']} failed rules, {snap['failed_checks']} failed checks)."


def _summary(analysis, tv: TagResult, tm: TagResult | None, enr: EnrichmentStats, before: dict, after: dict,
             comparison: dict, destructive_pages: list[int], warnings: list[str], title: str, language: str) -> str:
    lines = [_verdict_line("Before", before), _verdict_line("After", after)]
    et = tv.existing_tags
    if et.get("present"):
        if et.get("rebuilt_tree"):
            lines.append(f"Existing tags discarded and rebuilt ({et.get('reason')}).")
        else:
            lines.append(f"Existing tags: {et['retained']} retained, {et['repaired']} repaired, "
                         f"{et['replaced']} replaced, {et.get('removed', 0)} empty removed, "
                         f"{et.get('artifacted', 0)} decorative figure(s) made artifacts; "
                         f"{tv.orphans_retagged} orphaned content item(s) re-tagged.")
        for f in et.get("findings", [])[:5]:
            lines.append(f"  - {f}")
    el = tv.elements
    headings = sum(v for k, v in el.items() if k.startswith("H") and k[1:].isdigit())
    lines.append(
        f"Tags added: {tv.tagged_units} content sequences tagged, {tv.artifacts} marked as artifacts; "
        f"{headings} heading(s), {el.get('P', 0)} paragraph(s), {el.get('L', 0)} list(s), "
        f"{tv.tables} table(s), {tv.figures_with_alt + tv.figures_without_alt} figure(s), {tv.links} link(s)."
    )
    lines.append(f"Alt text: {enr.images_described} generated by AI, {enr.images_decorative} image(s) judged decorative"
                 + (f", {tv.figures_without_alt} figure(s) still without alt text" if tv.figures_without_alt else "") + ".")
    if enr.tables_analyzed:
        lines.append(f"Tables: {enr.tables_analyzed} analyzed by AI for header rows/columns and merged cells"
                     + (f"; {len(enr.layout_tables)} identified as layout tables" if enr.layout_tables else "") + ".")
    if enr.ocr:
        words = sum(len(o["changes"]) for o in enr.ocr if o["mode"] == "correct")
        lines_t = sum(1 for o in enr.ocr if o["mode"] == "transcribe")
        lines.append(f"OCR: the AI corrected {words} uncertain word(s) and transcribed {lines_t} line(s) Tesseract "
                     "couldn't read (each is flagged for review)"
                     + (f"; {enr.ocr_rejected} AI correction(s) failed the checks and weren't used" if enr.ocr_rejected else "")
                     + ".")
    if enr.notation:
        lines.append(f"Specialist notation: {len(enr.notation)} block(s) the layout rules couldn't read were read by AI "
                     "(each is flagged for review)"
                     + (f"; {enr.notation_rejected} AI reading(s) didn't match the page and weren't used" if enr.notation_rejected else "")
                     + ".")
    if enr.reading_order_pages:
        lines.append(f"Reading order: determined by AI on {enr.reading_order_pages} page(s).")
    lines.append(f"Document: title \"{title}\", language {language}, PDF/UA identifier set.")
    vf = comparison.get("visual_fidelity", {})
    lines.append(f"Visual fidelity of the non-destructive output: {vf.get('pages_identical')}/{vf.get('pages_compared')} pages pixel-identical.")
    if comparison.get("glyph_lists_removed"):
        lines.append(f"Font descriptors: incomplete glyph lists removed from {len(comparison['glyph_lists_removed'])} "
                     "font(s) (they're optional and unused for drawing; rendering unchanged).")
    if comparison.get("unicode_maps_added"):
        lines.append(f"Unicode maps completed for {len(comparison['unicode_maps_added'])} font(s) "
                     f"({'; '.join(comparison['unicode_maps_added'][:4])}) so symbols can be read aloud; rendering unchanged.")
    if comparison.get("glyph_widths_modified_only"):
        lines.append(f"Font metrics: {comparison['glyph_widths_modified_only']} glyph width(s) corrected in the "
                     "modified file only (the shifts are a fraction of a point, invisible but not pixel-identical).")
    if tv_widths := comparison.get("glyph_widths_compensated"):
        lines.append(f"Font metrics: {tv_widths} glyph(s) whose declared width disagreed with the embedded font "
                     "corrected, with spacing compensated so the page renders identically.")
    fonts_embedded = comparison.get("fonts_embedded") or []
    if fonts_embedded:
        lines.append(f"Modified version: embedded metric-compatible substitutes for {len(fonts_embedded)} "
                     f"unembedded font(s) ({'; '.join(fonts_embedded[:6])}); tags unchanged.")
    if destructive_pages:
        lines.append(
            f"Destructive changes on page(s) {', '.join(str(p + 1) for p in destructive_pages)} "
            "(flattened to an image with an invisible, tagged text layer in an embedded font). Two files are returned: "
            "_compliant_modified.pdf (primary) and _compliant_visual.pdf (visually exact)."
        )
    remaining = after.get("verapdf", {})
    for fl, v in remaining.items():
        clauses = v.get("failed_clauses") or []
        if clauses:
            lines.append(f"Remaining {fl} failures (clauses): {', '.join(clauses[:15])}.")
    for w in warnings:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


async def _tagmap_file(out: Path, stem: str, label: str, pdf_path: Path, data: dict | None = None) -> str | None:
    """Write the viewer's tag map for one file; returns its name, or None if it couldn't be built."""
    try:
        if data is None:
            data = await asyncio.to_thread(build_tagmap, pdf_path)
    except Exception as exc:  # noqa: BLE001 - the viewer is optional; never fail a job over it
        log.warning("tag map for %s failed: %s", label, exc)
        return None
    name = f"{stem}_tagmap_{label}.json"
    (out / name).write_text(json.dumps(data, separators=(",", ":")))
    return name


def _write_json_report(out: Path, stem: str, result: dict, store: JobStore | None = None) -> None:
    from app.reports.markdown import render_job_markdown

    name, md_name = f"{stem}_remediation_report.json", f"{stem}_remediation_report.md"
    result["full_report"] = name
    result["markdown_report"] = md_name
    (out / name).write_text(json.dumps(result, indent=2, default=str))
    meta = (store.read_meta() if store else None) or {"filename": f"{stem}.pdf"}
    # Links in the file are sibling file names, so they resolve when the outputs are downloaded together.
    (out / md_name).write_text(render_job_markdown(store.job_id if store else "", meta, "completed", result))
    # The same report for people: an accessible (PDF/UA-1) PDF, checked like any other output
    from app.reports.pdf import write_report_pdf
    pdf_name = f"{stem}_remediation_report.pdf"
    check = write_report_pdf(out / pdf_name, meta, result, (meta.get("flags") or {}).get("report_branding"))
    if (out / pdf_name).exists():
        result["report_pdf"] = pdf_name
        result["report_pdf_compliant"] = check.get("compliant")
        (out / name).write_text(json.dumps(result, indent=2, default=str))
