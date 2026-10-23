"""Tag a document with Adobe PDF Services Autotag and report on what comes back.

  1. Validate the original (veraPDF + internal audit)          -> "before" report
  2. Split if it's beyond Adobe's per-request limits; send each part to Adobe (through the shared rate
     limiter); merge the tagged parts back onto the original
  3. Validate Adobe's output as it is (veraPDF, tag integrity, tag review)  -> "after" report
  4. With an AI model: write alt text for the figures Adobe left without it, and readings of specialist notation
     (math, chemistry, doses) as ActualText on Adobe's text tags, as tag edits to a separate copy (pixel-identical
     to Adobe's), with the PDF/UA identifier, a usable title and header cells' Scope that Adobe leaves out, and
     validate that copy too  -> "enhanced"

Adobe's file is always kept unchanged (merged, for a large document). Problems in it are reported,
never fixed in it; the alt text and readings go only into the separate copy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time

from app.adobe import chunks
from app.adobe.client import AdobeClient, AdobeError
from app.ai.base import AIError
from app.ai.registry import get_provider
from app.pipeline import limits, mathml, protection
from app.pipeline.enrichment import EnrichmentStats
from app.config import get_settings
from app.pipeline.orchestrator import (
    JobOptions, Progress, _ai_read, _bookmarks, _delta, _describe_regions, _math_outputs, _move_xml, _primary,
    _read_formulas, _snapshot, _tagmap_file, _validate, _verdict_line, _visual_fidelity, _write_json_report,
    _xml_name,
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
    warnings: list[str] = protection.notes(protection.inspect(src), opts.report_only)  # signed, restricted (#339)

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

    # Improved copy: AI alt text, document fixes and lossless font fixes. Adobe's file stays as it is. ----------------
    enhanced_name = None
    vera_enhanced = None
    enrichment = EnrichmentStats()
    improvements: dict = {"alt_text": 0, "decorative": 0, "notation": 0, "title": None, "pdfua_identifier": False,
                          "header_scope": 0, "unicode_maps": [], "glyph_lists": [], "glyph_widths": 0}
    ai_stats = {"calls": 0, "failures": 0, "cost": 0.0, "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
                "model": None}
    candidate = work / "improved.pdf"
    shutil.copyfile(primary, candidate)
    ai = get_provider(opts.provider, opts.model, log_path=(work / "ai_log.jsonl") if opts.debug else None)
    if ai is not None:
        progress("ai_alt_text", 82)
        edits = await _ai_edits(ai, opts, primary, src, out / (tag_maps["primary"] or ""), primary_map,
                                enrichment, warnings, ai_stats)
        if edits:
            from app.tagging.editor import EditError, apply_edits
            try:
                await asyncio.to_thread(apply_edits, primary, candidate, edits)
            except EditError as exc:
                warnings.append(f"the AI alt text and readings couldn't be added to Adobe's tags: {exc}")
                shutil.copyfile(primary, candidate)
                enrichment.notation = []
            else:
                improvements["alt_text"] = sum(1 for e in edits
                                               if e["op"] == "set_alt" and e["expect_role"] == "Figure")
                improvements["decorative"] = sum(1 for e in edits if e["op"] == "set_decorative")
                improvements["notation"] = sum(1 for e in edits if e["op"] == "set_actual_text"
                                               or (e["op"] == "set_alt" and e["expect_role"] == "Formula"))
    # The readings that reached Adobe's tags, as MathML and read by rule (#403), before the copy is checked
    await _read_formulas(enrichment, (), src)
    # What PDF/UA asks of the document that Adobe leaves out: the identifier, a title that isn't "Microsoft Word -
    # x.docx", and Scope on header cells (before the font fixes, whose veraPDF check is the copy's)
    tagmap = primary_map
    if tagmap is None and tag_maps["primary"] and (out / tag_maps["primary"]).is_file():
        tagmap = json.loads((out / tag_maps["primary"]).read_text())
    improvements.update(await asyncio.to_thread(_document_fixes, candidate, tagmap, opts))
    progress("font_fixes", 86)
    improvements.update(await asyncio.to_thread(_font_fixes, primary, candidate, work, stem, warnings))
    vera_candidate = improvements.pop("vera")
    # Adobe doesn't write bookmarks. Its tags' headings are the copy's (alt text and font fixes don't touch them),
    # so Adobe's tag map says where they go
    # (after any veraPDF check of the copy, which doesn't look at the outline)
    improvements["bookmarks"] = (await _bookmarks(candidate, primary_map))[0]["bookmarks"]
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
                judge_model=opts.judge_model,
                # the AI's readings are listed for a person to check, as on the standard route
                ai_read=_ai_read(enrichment)))
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
        "notation_read_by_ai": sum(1 for n in enrichment.notation if not n.get("by_rule")),
        "notation_by_rule": sum(1 for n in enrichment.notation if n.get("by_rule")),
        "notation_ai_rejected": enrichment.notation_rejected,
        "notation_ai_rejected_by_reason": dict(sorted(enrichment.notation_rejected_by.items(), key=lambda kv: -kv[1])),
        "limits_hit": limits.collect(enrichment.limits_hit, verification, enrichment.findings,
                                     before_audit.get("pages") or pages),
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
    # Formulas read onto Adobe's tags, as MathML too (#403)
    math_formulas, math_files = await _math_outputs(out, stem, enrichment)
    if enrichment.math_formulas:
        summary.append(f"Formulas: {enrichment.math_formulas} saved as MathML as well, in the math files and the "
                       "formula list.")
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
            # Specialist notation read onto Adobe's tags (page 1-based, region, kind, read-aloud text, LaTeX,
            # confidence)
            "ai_notation": [{**n, "page": n["page"] + 1} for n in enrichment.notation][:300] or None,
            "math": [{**f, "page": f["page"] + 1} for f in math_formulas][:300] or None,
            "math_disagreements": mathml.disagreements(math_formulas) or None,
            **{k: math_files.get(k) for k in ("math_files", "math_list", "math_nemeth", "math_ueb")},
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


def _document_fixes(path, tagmap: dict | None, opts: JobOptions) -> dict:
    """Fixes to the document that never change how it looks, applied to path in place: the PDF/UA identifier
    (5-1), a title in place of one that is only the application and file name, and Scope on the header cells whose
    rows say what they head (7.5-1). Returns what was done."""
    import pikepdf

    from app.pipeline.orchestrator import usable_title
    from app.tagging.metadata import document_info, fix_dates, set_title
    from app.tagging.self_links import drop_self_links

    done: dict = {"title": None, "pdfua_identifier": False, "header_scope": 0, "self_links": 0, "dates": 0}
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        title = document_info(pdf)["title"]
        new = opts.title or (None if usable_title(title) else _heading_title(tagmap) or opts.stem.replace("_", " "))
        if new and new != title:
            set_title(pdf, new)
            done["title"] = new
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            if not meta.get("pdfuaid:part"):
                part = 2 if pdf.pdf_version >= "2.0" else 1  # PDF/UA-2 is for PDF 2.0 files
                meta["pdfuaid:part"] = str(part)
                if part == 2:
                    meta["pdfuaid:rev"] = "2024"
                done["pdfua_identifier"] = True
        done["header_scope"] = _header_scopes(pdf)
        done["self_links"] = drop_self_links(pdf)  # links that point to themselves (#484)
        done["dates"] = fix_dates(pdf)  # dates PAC can read (#483)
        if any(done.values()):
            pdf.save(path)
    return done


def _heading_title(tagmap: dict | None) -> str | None:
    """The first top-level heading's text in Adobe's tags (Adobe's Title tag is one), else the first heading's."""
    heads = [e for e in (tagmap or {}).get("elements", [])
             if re.fullmatch(r"H\d?", e.get("role") or "") and (e.get("text") or "").strip()]
    first = next((e for e in heads if e["role"] == "H1"), heads[0] if heads else None)
    return " ".join(first["text"].split())[:200] if first else None


def _header_scopes(pdf) -> int:
    """Scope on Adobe's TH cells that have none, where the row says it: a row of header cells (or one in THead)
    heads columns; the first cell of a row of data heads its row. Any other header cell is left as it is, and so is a
    table whose cells name their headers. Returns how many were given one."""
    from pikepdf import Array, Dictionary

    from app.tagging.existing_tags import _is_struct_elem, _role_map, resolve_role

    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    roles = _role_map(root)

    def kids(e) -> list:
        k = e.get("/K")
        return [x for x in (list(k) if isinstance(k, Array) else [k]) if _is_struct_elem(x)]

    def role(e) -> str:
        return resolve_role(str(e.get("/S", ""))[1:], roles)

    def attrs(e) -> list:
        a = e.get("/A")
        return [x for x in (list(a) if isinstance(a, Array) else [a]) if isinstance(x, Dictionary)]

    def walk(e, seen: set):
        for k in kids(e):
            if k.is_indirect:
                if k.objgen in seen:
                    continue
                seen.add(k.objgen)
            yield k
            yield from walk(k, seen)

    given = 0
    for table in [e for e in walk(root, set()) if role(e) == "Table"]:
        rows: list[tuple[object, bool]] = []  # (row, in THead)
        for k in kids(table):
            r = role(k)
            if r == "TR":
                rows.append((k, False))
            elif r in ("THead", "TBody", "TFoot"):
                rows += [(x, r == "THead") for x in kids(k) if role(x) == "TR"]
        if any("/Headers" in c for row, _ in rows for c in kids(row)):
            continue
        for row, head in rows:
            mates = [x for x in kids(row) if role(x) in ("TH", "TD")]
            header_row = head or all(role(x) == "TH" for x in mates)
            for n, c in enumerate(mates):
                if role(c) != "TH" or any("/Scope" in a for a in attrs(c)):
                    continue
                if header_row:
                    scope = "Column"
                elif n == 0:
                    scope = "Row"
                else:
                    continue
                _set_scope(c, scope)
                given += 1
    return given


def _set_scope(c, scope: str) -> None:
    from pikepdf import Array, Dictionary, Name

    attrs = c.get("/A")
    table = next((a for a in (list(attrs) if isinstance(attrs, Array) else [attrs])
                  if isinstance(a, Dictionary) and a.get("/O") == Name.Table), None)
    if table is not None:
        table.Scope = Name("/" + scope)
    elif attrs is None:
        c.A = Dictionary(O=Name.Table, Scope=Name("/" + scope))
    elif isinstance(attrs, Array):
        attrs.append(Dictionary(O=Name.Table, Scope=Name("/" + scope)))
    else:
        c.A = Array([attrs, Dictionary(O=Name.Table, Scope=Name("/" + scope))])


def _describe(imp: dict) -> str:
    parts = []
    if imp.get("alt_text"):
        parts.append(f"AI alt text for {imp['alt_text']} figure(s)")
    if imp.get("decorative"):
        parts.append(f"{imp['decorative']} figure(s) judged decorative")
    if imp.get("notation"):
        parts.append(f"specialist notation read aloud correctly in {imp['notation']} tag(s)")
    if imp.get("glyph_lists"):
        parts.append(f"incomplete glyph lists removed from {len(imp['glyph_lists'])} font(s)")
    if imp.get("unicode_maps"):
        parts.append(f"Unicode maps completed for {len(imp['unicode_maps'])} font(s)")
    if imp.get("glyph_widths"):
        parts.append(f"{imp['glyph_widths']} glyph width(s) corrected")
    if imp.get("bookmarks"):
        parts.append(f"{imp['bookmarks']} bookmark(s) made from the headings")
    if imp.get("title"):
        parts.append(f"title \"{imp['title']}\" in place of Adobe's")
    if imp.get("pdfua_identifier"):
        parts.append("PDF/UA identifier added")
    if imp.get("header_scope"):
        parts.append(f"Scope given to {imp['header_scope']} table header cell(s)")
    if imp.get("self_links"):
        parts.append(f"{imp['self_links']} link(s) that only pointed to themselves removed")
    if imp.get("dates"):
        parts.append(f"{imp['dates']} date(s) that weren't valid PDF dates rewritten or removed")
    return ("Changes: " + "; ".join(parts) + ".") if parts else ""


async def _ai_edits(ai, opts: JobOptions, pdf, original, tagmap_path, tagmap: dict | None,
                    enrichment: EnrichmentStats, warnings: list[str], stats: dict) -> list[dict]:
    """Tag edits that give every Figure without alt text a description (or mark it decorative), and Adobe's text tags
    whose notation the PDF's own text can't voice a reading as ActualText. The notation is found and read on the
    original, as on the standard route: Adobe's file draws the same pages, but its text, extracted, can carry
    ActualText of its tags in place of what is drawn, and the readings are checked against what is drawn."""
    import json

    from app.pipeline.enrichment import read_notation
    from app.pipeline.notation import has_doses, need
    from app.pipeline.orchestrator import prepare_analysis

    if tagmap is None:
        tagmap = json.loads(tagmap_path.read_text()) if tagmap_path.is_file() else {"elements": []}
    figures = [e for e in tagmap.get("elements", [])
               if e.get("role") == "Figure" and not (e.get("alt") or "").strip() and e.get("boxes")]
    limit = get_settings().ai_existing_figures_max
    if len(figures) > limit:
        enrichment.limit_hit("AI_EXISTING_FIGURES_MAX", limit, len(figures),
                             "figures weren't described by the AI and have no alt text")
        enrichment.findings.append(f"{len(figures) - limit} figure(s) beyond the limit of {limit} "
                                   "(AI_EXISTING_FIGURES_MAX) weren't described by the AI and have no alt text")
    figures = figures[:limit]
    # Adobe's tags say what is a paragraph or a formula, but not where the sub- and superscripts are: our own look at
    # the pages finds the notation, as on the standard route, and its readings go onto Adobe's tags that hold it
    analysis = None
    if opts.notation_ai and get_settings().ai_notation_enabled:
        analysis = await asyncio.to_thread(prepare_analysis, original)
        if not any(need(b) for p in analysis.pages for b in p.blocks) and \
                not any(has_doses(c.text or "") for p in analysis.pages for t in p.tables for c in t.cells):
            analysis = None  # nothing the rules can't read: no AI needed for it
    if not figures and analysis is None:
        return []
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
        described = await _describe_regions(ai, pdf, regions, enrichment) if figures else []
        if analysis is not None:
            before = len(enrichment.findings)
            await read_notation(ai, original, analysis, enrichment)
            # Each region's "read by AI; check it" is said once below, for what reached Adobe's tags; what wasn't used
            # (didn't match the page, failed) stays as it is
            enrichment.findings[before:] = [f for f in enrichment.findings[before:] if "notation read by AI (" not in f]
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
    if analysis is not None:
        read, unplaced = notation_edits(analysis, tagmap)
        edits += read
        # Only what reached a tag is reported, and listed for review
        placed = {tuple(e["bbox"]) for e in read}
        enrichment.notation = [n for n in enrichment.notation if tuple(n["bbox"]) in placed]
        by_ai = sum(1 for n in enrichment.notation if not n.get("by_rule"))
        if read:
            enrichment.findings.insert(0, f"{len(read)} reading(s) of specialist notation written onto Adobe's tags as "
                                          f"read-aloud text ({by_ai} read by AI: check them)")
        if unplaced:
            enrichment.findings.insert(0, f"{unplaced} reading(s) of specialist notation matched no text tag of "
                                          "Adobe's (Adobe's tags divide that text differently, or already say "
                                          "something of their own); read as Adobe tagged it")
        for e in read:
            del e["bbox"]
    return edits


# Adobe's tags that can take a reading: text tags with nothing of their own to say, holding only plain Spans (a list
# item: its label and body)
_TEXT_ROLES = {"P", "LBody", "LI", "Lbl", "TD", "TH", "Span", "Caption", "Formula", "H", "H1", "H2", "H3", "H4", "H5",
               "H6", "Quote", "BlockQuote", "Note"}


def _same_text(tag: str, block: str) -> str | None:
    """Whether the tag holds the block's text: "exact" for the same characters, whatever the spacing and line-end
    hyphens, and in any order (the lines of a table cell can come out in another order), a character with no readable
    meaning matching any (the two ways of extracting text decode such a glyph differently, and those are the blocks
    that need a reading). "loose" for the same letters and digits in any order, whatever the symbols and marks between
    them: math comes out of the two in another order, an accent as a mark over its letter in one and a character
    before it in the other. None otherwise. The tag map's text stops at 200 characters, so a tag's text that long is
    compared as far as it goes."""
    from collections import Counter

    from app.pipeline.notation import _UNREADABLE
    from app.verification.tagmap import TEXT_CHARS

    t, b = (re.sub(r"[\s\-\u00ad]", "", x) for x in (tag, block))
    if not t:
        return None
    if len(tag) >= TEXT_CHARS:
        b = b[:len(t)]
    if t == b or Counter(t) == Counter(b):
        return "exact"
    readable = [(x, y) for x, y in zip(t, b) if not (_UNREADABLE.match(x) or _UNREADABLE.match(y))]
    if len(t) == len(b) and len(readable) < len(t) and all(x == y for x, y in readable):
        return "exact"
    letters = [Counter(c for c in x if c.isalnum() and c.isascii()) for x in (t, b)]
    if sum(letters[0].values()) >= 2 and letters[0] == letters[1]:
        return "loose"
    return None


def notation_edits(analysis, tagmap: dict) -> tuple[list[dict], int]:
    """set_actual_text edits (set_alt on a Formula) that put the analysis's notation readings (and doses read by rule,
    in blocks and table cells) onto Adobe's tags: the tag on the same page that holds the reading's text, where the two
    boxes overlap (the closest overlap if two do; at least half, when only the letters and digits agree). Boxes alone
    don't say it: a tag's box hugs its glyphs, a block's spans its lines. Nor does text alone: math fonts come out of
    the two ways of extracting text as different letters, so a tag whose box is the reading's (85% overlap) takes it
    whatever its text. Each edit carries the reading's box ("bbox", the analysis's) for the caller. Returns the edits
    and how many readings found no such tag."""
    from app.pipeline.models import bbox_area, bbox_intersection
    from app.pipeline.notation import has_doses

    wanted: list[tuple[int, tuple, str, str]] = []
    for page in analysis.pages:
        for b in page.blocks:
            if b.notation_reading and b.actual_text and b.role != "Artifact":
                wanted.append((page.number, tuple(b.bbox), b.text, b.actual_text))
        for table in page.tables:
            for c in table.cells:
                if c.actual_text and c.bbox and has_doses(c.text or ""):
                    wanted.append((page.number, tuple(c.bbox), c.text or "", c.actual_text))
    if not wanted:
        return [], 0
    els = {e["i"]: e for e in tagmap.get("elements", [])}

    def quiet(e) -> bool:
        return not (e.get("alt") or "").strip() and not (e.get("actual_text") or "").strip() and not e.get("annot")

    def plain(e, inside: set[str]) -> bool:
        """Everything inside is plain: roles from inside, nothing to say of their own."""
        for k in e.get("kids") or []:
            kid = els.get(k)
            if kid is None or kid.get("role") not in inside or not quiet(kid) or not plain(kid, {"Span"}):
                return False
        return True

    candidates: dict[int, list] = {}
    for e in els.values():
        boxes = e.get("boxes") or []
        if e.get("role") not in _TEXT_ROLES or not quiet(e) or len(boxes) != 1 or not e.get("text"):
            continue
        if not plain(e, {"Span", "Lbl", "LBody"} if e["role"] == "LI" else {"Span"}):
            continue
        candidates.setdefault(boxes[0]["page"] - 1, []).append((e, tuple(boxes[0]["bbox"])))
    edits, unplaced, used = [], 0, set()
    for page, box, text, said in wanted:
        best, best_score = None, 0.0
        for e, eb in candidates.get(page, []):
            if e["i"] in used:
                continue
            same = _same_text(e["text"], text)
            inter = bbox_intersection(box, eb)
            union = bbox_area(box) + bbox_area(eb) - inter
            score = inter / union if union > 0 else 0.0
            if same == "loose" and score < 0.5:
                continue  # only the letters agree: the boxes must too
            if same is None and score < 0.85:
                continue  # the text disagrees (math fonts decode differently): only the same box will do
            if score > best_score:
                best, best_score = e, score
        if best is not None:
            used.add(best["i"])
            # A formula's spoken form is its alt text, as on the standard route (PDF/UA asks a Formula for one)
            if best["role"] == "Formula":
                edits.append({"op": "set_alt", "element": best["i"], "expect_role": "Formula", "alt": said,
                              "bbox": list(box)})
            else:
                edits.append({"op": "set_actual_text", "element": best["i"], "expect_role": best["role"], "text": said,
                              "bbox": list(box)})
        else:
            unplaced += 1
    return edits, unplaced


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
