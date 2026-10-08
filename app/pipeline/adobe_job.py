"""Tag a document with Adobe PDF Services Autotag and report on what comes back.

  1. Validate the original (veraPDF + internal audit)          -> "before" report
  2. Split if it's beyond Adobe's per-request limits; send each part to Adobe (through the shared rate
     limiter); merge the tagged parts back onto the original
  3. Validate Adobe's output as it is (veraPDF, tag integrity, tag review)  -> "after" report
  4. With an AI model: write alt text for the figures Adobe left without it, as tag edits to a
     separate copy (pixel-identical to Adobe's), and validate that copy too  -> "enhanced"

Adobe's file is always kept unchanged (merged, for a large document). Problems in it are reported,
never fixed in it; the alt text goes only into the separate copy.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from app.adobe import chunks
from app.adobe.client import AdobeClient, AdobeError
from app.ai.base import AIError
from app.ai.registry import get_provider
from app.pipeline.enrichment import EnrichmentStats
from app.config import get_settings
from app.pipeline.orchestrator import (
    MAX_REGION_FIGURES, JobOptions, Progress, _delta, _describe_regions, _move_xml, _primary, _snapshot, _tagmap_file, _validate, _verdict_line,
    _visual_fidelity, _write_json_report, _xml_name,
)
from app.storage import JobStore
from app.validation.audit import audit_pdf
from app.verification.verify import VerifyOptions, verify_output

log = logging.getLogger(__name__)


async def run_adobe(store: JobStore, opts: JobOptions, progress: Progress = lambda s, p: None,
                    client: AdobeClient | None = None) -> dict:
    s = get_settings()
    if not s.adobe_enabled:
        raise AdobeError("adobe_disabled", "Adobe Autotag isn't switched on for this engine")
    started = time.monotonic()
    work, out, stem = store.work_dir, store.output_dir, opts.stem
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    src = store.input_pdf
    warnings: list[str] = []

    progress("validating_original", 5)
    before_audit = await asyncio.to_thread(audit_pdf, src)
    before_vera = await asyncio.to_thread(_validate, src, out, "before", stem)
    if not _primary(before_vera):
        warnings.append(f"veraPDF unavailable: {next(iter(before_vera.values())).error}")

    # Adobe ------------------------------------------------------------------------------------
    progress("adobe_sending", 15)
    parts = await asyncio.to_thread(chunks.split, src, work, s.adobe_max_pages_per_call, s.adobe_max_mb_per_call)
    done = 0

    async def one(c: AdobeClient, part: chunks.Part):
        nonlocal done
        res = await c.autotag(part.path, work, f"adobe_{part.first + 1:05d}")
        done += 1
        progress("adobe_tagging", 15 + int(50 * done / len(parts)))
        return res

    own = client is None
    c = client or AdobeClient()
    try:
        results = await asyncio.gather(*(one(c, p) for p in parts))
    finally:
        if own:
            await c.__aexit__(None, None, None)

    primary_name = f"{stem}_adobe_tagged.pdf"
    primary = out / primary_name
    merge_notes: list[str] = []
    if len(parts) == 1:
        shutil.copyfile(results[0].tagged_pdf, primary)
    else:
        progress("adobe_merging", 68)
        merge_notes = await asyncio.to_thread(chunks.merge, src, [(p, r.tagged_pdf) for p, r in zip(parts, results)],
                                              primary)
        warnings += [f"merging Adobe's parts: {n}" for n in merge_notes]
    adobe_reports = []
    for i, r in enumerate(results):
        if r.report is not None:
            name = f"{stem}_adobe_report.xlsx" if len(results) == 1 else f"{stem}_adobe_report_part{i + 1}.xlsx"
            shutil.copyfile(r.report, out / name)
            adobe_reports.append(name)

    # After ------------------------------------------------------------------------------------
    progress("validating_output", 72)
    vera_after = await asyncio.to_thread(_validate, primary, work, "after", stem)
    tag_maps = {"original": await _tagmap_file(out, stem, "original", src)}
    verification = None
    primary_map = None
    if s.verify_enabled:
        progress("verifying_tags", 78)
        # No AI review of Adobe's own file: it has no alt text to review (the AI copy below is reviewed).
        rep = await verify_output(primary, VerifyOptions(ai=False, seed=opts.job_id))
        verification = {"primary": rep.to_dict()}
        primary_map = rep.tagmap
        if not rep.integrity_passed:
            warnings.append(f"Adobe's tags have {len(rep.errors)} structural problem(s); see the tag review. "
                            "The file is kept as Adobe returned it")
    tag_maps["primary"] = await _tagmap_file(out, stem, "primary", primary, primary_map)

    # Improved copy: AI alt text and lossless font fixes. Adobe's file stays as it is. ----------------
    enhanced_name = None
    vera_enhanced = None
    enrichment = EnrichmentStats()
    improvements: dict = {"alt_text": 0, "decorative": 0, "unicode_maps": [], "glyph_lists": [], "glyph_widths": 0}
    ai_stats = {"calls": 0, "failures": 0, "cost": 0.0, "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
                "model": None}
    candidate = work / "improved.pdf"
    shutil.copyfile(primary, candidate)
    ai = get_provider(opts.provider, opts.model, log_path=(work / "ai_log.jsonl") if opts.debug else None)
    if ai is not None:
        progress("ai_alt_text", 82)
        edits = await _alt_text_edits(ai, opts, primary, out / (tag_maps["primary"] or ""), primary_map,
                                      enrichment, warnings, ai_stats)
        if edits:
            from app.tagging.editor import EditError, apply_edits
            try:
                await asyncio.to_thread(apply_edits, primary, candidate, edits)
            except EditError as exc:
                warnings.append(f"the AI alt text couldn't be added to Adobe's tags: {exc}")
                shutil.copyfile(primary, candidate)
            else:
                improvements["alt_text"] = sum(1 for e in edits if e["op"] == "set_alt")
                improvements["decorative"] = sum(1 for e in edits if e["op"] == "set_decorative")
    progress("font_fixes", 86)
    improvements.update(await asyncio.to_thread(_font_fixes, primary, candidate, work, stem, warnings))
    vera_candidate = improvements.pop("vera")
    changed = any(improvements[k] for k in improvements)
    if changed and not (await asyncio.to_thread(_visual_fidelity, primary, candidate))["identical"]:
        warnings.append("the improved copy didn't render identically to Adobe's file, so it was discarded")
        changed = False
    if changed:
        enhanced = out / f"{stem}_adobe_improved.pdf"
        shutil.copyfile(candidate, enhanced)
        enhanced_name = enhanced.name
        progress("validating_improved_copy", 90)
        vera_enhanced = vera_candidate or await asyncio.to_thread(_validate, enhanced, work, "after_enhanced", stem)
        if s.verify_enabled:
            # The AI review checks the AI's alt text; font fixes alone need no AI review.
            rep = await verify_output(enhanced, VerifyOptions(
                ai=opts.verify_ai and improvements["alt_text"] > 0, full_review=opts.full_review, seed=opts.job_id,
                judge_model=opts.judge_model))
            verification["enhanced"] = rep.to_dict()
            tag_maps["enhanced"] = await _tagmap_file(out, stem, "enhanced", enhanced, rep.tagmap)
            if rep.ai.get("error"):
                warnings.append(f"AI review did not run: {rep.ai['error']}")
        else:
            tag_maps["enhanced"] = await _tagmap_file(out, stem, "enhanced", enhanced)

    before_snapshot = _snapshot(before_audit, before_vera)
    after_snapshot = _snapshot(await asyncio.to_thread(audit_pdf, primary), vera_after)
    comparison = {
        "before": before_snapshot,
        "after": after_snapshot,
        "delta": _delta(before_snapshot, after_snapshot),
        "visual_fidelity": await asyncio.to_thread(_visual_fidelity, src, primary),
    }
    if enhanced_name:
        comparison["after_enhanced"] = _snapshot(await asyncio.to_thread(audit_pdf, out / enhanced_name),
                                                 vera_enhanced)
    pages = sum(p.pages for p in parts)
    adobe = {
        "parts": len(parts),
        "pages_sent": pages,
        "requests": sum(r.requests for r in results),
        "retries": sum(r.retries for r in results),
        "seconds": round(sum(r.seconds for r in results), 2),
        "jobs": [{"first_page": p.first + 1, "pages": p.pages, "adobe_job": r.adobe_job, "seconds": r.seconds,
                  "timings": r.timings} for p, r in zip(parts, results)],
        "base_url": s.adobe_base_url,
    }
    metrics = {
        "pages_processed": before_audit.get("pages") or pages,
        "processing_time_seconds": round(time.monotonic() - started, 2),
        "ai_calls": ai_stats["calls"], "ai_failures": ai_stats["failures"],
        "ai_cost_usd": round(ai_stats["cost"], 6), "ai_tokens_used": ai_stats["tokens"],
        "ai_provider": opts.provider, "ai_model": ai_stats["model"],
        "images_described": enrichment.images_described,
        "route": "adobe",
        "adobe": adobe,
    }
    review_ai = ((verification or {}).get("enhanced") or {}).get("ai") or {}
    if review_ai.get("ai_tokens_used"):
        metrics["verification_ai_cost_usd"] = round(float(review_ai["ai_tokens_used"].get("cost_usd", 0.0)), 6)

    summary = [_verdict_line("Before", before_snapshot), _verdict_line("After (Adobe)", after_snapshot),
               f"Tagged by Adobe PDF Services Autotag: {pages} page(s)"
               + (f" in {len(parts)} parts, merged into one document" if len(parts) > 1 else "") + ".",
               "The file is Adobe's output unchanged. Adobe doesn't write alt text for figures, and its tags "
               "aren't guaranteed to meet PDF/UA."]
    if enhanced_name:
        summary.append(f"Improved copy ({enhanced_name}); Adobe's file is unchanged. {_describe(improvements)} "
                       + _verdict_line("Improved copy", comparison["after_enhanced"]))
    vf = comparison["visual_fidelity"]
    summary.append(f"Visual fidelity: {vf.get('pages_identical')}/{vf.get('pages_compared')} pages pixel-identical "
                   "to the original.")
    summary += [f"Warning: {w}" for w in warnings]
    result = {
        "files": {"remediated_primary": primary_name, "remediated_visual_fallback": None,
                  "enhanced": enhanced_name},
        "metrics": metrics,
        "reports": {
            "verapdf_before": _xml_name(before_vera),
            "verapdf_after": {"primary": _move_xml(vera_after, out, f"{stem}_verapdf_after"), "visual_fallback": None,
                              "enhanced": _move_xml(vera_enhanced, out, f"{stem}_verapdf_after_improved")},
            "improvements": improvements if enhanced_name else None,
            "comparison": comparison,
            "remediation_summary": "\n".join(summary),
            "tag_verification": verification,
            "tag_maps": tag_maps,
            "adobe_reports": adobe_reports or None,
            "ai_findings": enrichment.findings[:100],
        },
        "warnings": warnings,
    }
    _write_json_report(out, stem, result, store)
    progress("completed", 100)
    return result


def _font_fixes(primary, pdf, work, stem: str, warnings: list[str]) -> dict:
    """Font fixes that never change how pages look, applied to pdf in place: missing ToUnicode maps,
    incomplete glyph lists (7.21.4.2) and glyph width mismatches (7.21.5; kept only if the pages stay
    pixel-identical to Adobe's file). Returns what was done, and the last veraPDF result ("vera")."""
    from app.pipeline.orchestrator import _fix_glyph_lists, _fix_widths
    from app.tagging.font_unicode import add_missing_tounicode

    out: dict = {"unicode_maps": [], "glyph_lists": [], "glyph_widths": 0, "vera": None}
    tmp = work / "improved_unicode.pdf"
    out["unicode_maps"] = add_missing_tounicode(pdf, tmp)
    if out["unicode_maps"]:
        tmp.replace(pdf)
    vera = _validate(pdf, work, "after_enhanced", stem)
    vera, out["glyph_lists"] = _fix_glyph_lists(pdf, vera, work, "after_enhanced", stem)
    before = work / "improved_before_widths.pdf"
    shutil.copyfile(pdf, before)
    vera_before = vera
    vera, widths = _fix_widths(pdf, vera, work, "after_enhanced", stem)
    if widths and not _visual_fidelity(primary, pdf)["identical"]:
        shutil.copyfile(before, pdf)
        vera, widths = vera_before, 0
        warnings.append("glyph width corrections would move text by a fraction of a point, so they weren't applied "
                        "to the improved copy")
    out["glyph_widths"] = widths
    out["vera"] = vera
    return out


def _describe(imp: dict) -> str:
    parts = []
    if imp.get("alt_text"):
        parts.append(f"AI alt text for {imp['alt_text']} figure(s)")
    if imp.get("decorative"):
        parts.append(f"{imp['decorative']} figure(s) judged decorative")
    if imp.get("glyph_lists"):
        parts.append(f"incomplete glyph lists removed from {len(imp['glyph_lists'])} font(s)")
    if imp.get("unicode_maps"):
        parts.append(f"Unicode maps completed for {len(imp['unicode_maps'])} font(s)")
    if imp.get("glyph_widths"):
        parts.append(f"{imp['glyph_widths']} glyph width(s) corrected")
    return ("Changes: " + "; ".join(parts) + ".") if parts else ""


async def _alt_text_edits(ai, opts: JobOptions, pdf, tagmap_path, tagmap: dict | None,
                          enrichment: EnrichmentStats, warnings: list[str], stats: dict) -> list[dict]:
    """Tag edits that give every Figure without alt text a description (or mark it decorative)."""
    import json

    if tagmap is None:
        tagmap = json.loads(tagmap_path.read_text()) if tagmap_path.is_file() else {"elements": []}
    figures = [e for e in tagmap.get("elements", [])
               if e.get("role") == "Figure" and not (e.get("alt") or "").strip() and e.get("boxes")]
    if not figures:
        return []
    figures = figures[:MAX_REGION_FIGURES]
    ai.tags = {"job": opts.job_id, **({"tier": opts.ai_tier} if opts.ai_tier else {})}
    async with ai:
        try:
            await ai.preflight()
        except AIError as exc:
            warnings.append(f"every AI call failed (the AI isn't available: {exc}); Adobe's file is returned "
                            "without AI alt text")
            stats["failures"] = 1
            return []
        regions = [(f["boxes"][0]["page"] - 1, tuple(f["boxes"][0]["bbox"])) for f in figures]
        described = await _describe_regions(ai, pdf, regions, enrichment)
    stats.update(calls=ai.calls, failures=ai.failures, cost=ai.usage.cost_usd, model=ai.model,
                 tokens={"prompt_tokens": ai.usage.prompt_tokens, "completion_tokens": ai.usage.completion_tokens})
    if ai.failures and not ai.calls:
        warnings.append(f"every AI call failed ({ai.failures}); Adobe's file is returned without AI alt text")
    by_region = {(p, tuple(b)): (alt, decorative) for p, b, alt, decorative in described}
    edits = []
    for f, region in zip(figures, regions):
        if region not in by_region:
            continue
        alt, decorative = by_region[region]
        if decorative:
            edits.append({"op": "set_decorative", "element": f["i"], "expect_role": "Figure"})
        else:
            edits.append({"op": "set_alt", "element": f["i"], "expect_role": "Figure", "alt": alt})
    return edits


def adobe_error(exc: AdobeError) -> dict:
    """The job error for an Adobe failure: a stable code for the portal, and Adobe's own detail."""
    if exc.code == "adobe_quota":
        code = "adobe_quota"
    elif exc.code in ("adobe_disabled", "not_configured"):
        code = "adobe_unavailable"
    elif exc.code in ("CORRUPT_DOCUMENT", "PASSWORD_PROTECTED", "BAD_PDF"):
        code = "adobe_rejected"
    else:
        code = "adobe_failed"
    return {"error": code, "detail": exc.message, "adobe_code": exc.code, "retryable": exc.retryable}
