"""Render a job result as a human-readable Markdown report."""
from __future__ import annotations

from typing import Callable

FileRef = Callable[[str | None], str | None]

_ROWS = [
    ("PDF/UA verdict", "compliant"),
    ("Failed rules", "failed_rules"),
    ("Failed checks", "failed_checks"),
    ("Tagged (structure tree)", "tagged"),
    ("Content tagged or artifact", "content_tagged_ratio"),
    ("Structure elements", "struct_elements"),
    ("Headings", "headings"),
    ("Heading level skips", "heading_level_skips"),
    ("Figures", "figures"),
    ("Figures missing alt text", "figures_missing_alt"),
    ("Tables", "tables"),
    ("Tables without headers", "tables_without_headers"),
    ("Untagged annotations", "annotations_untagged"),
    ("Font problems", "font_problems"),
    ("Language", "language"),
    ("Title", "title"),
    ("Title shown in viewer", "display_doc_title"),
    ("PDF/UA identifier", "pdfua_identifier"),
]


def _esc(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _fmt(key: str, v) -> str:
    if v is None:
        return "–"
    if key == "compliant":
        return "✅ Pass" if v else "❌ Fail"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if key == "content_tagged_ratio" and isinstance(v, (int, float)):
        return f"{v:.0%}"
    return _esc(v)


_SEV_LABEL = {"high": "🔴 High", "review": "🟠 Review", "suggest": "🔵 Suggest", "warning": "⚪ Note"}
_LABELS = {"primary": "remediated file", "visual_fallback": "visually exact file", "original": "original file"}


def _review_section(tv: dict | None, limit: int = 40) -> list[str]:
    if not tv:
        return []
    out = ["## Needs human review", ""]
    for label, rep in tv.items():
        if not rep:
            continue
        integ = rep.get("integrity", {})
        summ = rep.get("summary", {})
        ai = rep.get("ai") or {}
        status = "✅ passed" if integ.get("passed") else "❌ failed"
        line = (f"- **Tag check ({_LABELS.get(label, label)}):** structure integrity {status} "
                f"({summ.get('elements_checked', 0):,} tags checked); {summ.get('flagged', 0)} item(s) flagged")
        if ai.get("error"):
            line += f"; ⚠️ **AI review failed: {_esc(ai['error'])}**"
        elif ai.get("mode") and ai.get("mode") != "off":
            res = ai.get("results") or {}
            line += (f"; AI review ({ai['mode']}): {ai.get('reviewed', 0)} item(s) by "
                     f"{', '.join(ai.get('judges') or []) or 'no judge'}"
                     + (f", {res.get('inaccurate', 0)} alt text(s) judged wrong" if res else ""))
            if res.get("failed"):
                line += f", {res['failed']} review call(s) failed"
        elif ai.get("mode") == "off":
            line += "; AI review off"
        out.append(line + ".")
    out.append("")
    first = next((r for r in tv.values() if r), None)
    findings = (first or {}).get("findings") or []
    if not findings:
        out += ["Nothing flagged.", ""]
        return out
    out += ["| Severity | Page | Tag | Issue | Details | Suggested action |", "|---|---|---|---|---|---|"]
    for f in findings[:limit]:
        details = f.get("detail") or (f'"{f["text"]}"' if f.get("text") else "")
        conf = f" ({f['confidence']:.0%})" if isinstance(f.get("confidence"), (int, float)) else ""
        out.append(f"| {_SEV_LABEL.get(f.get('severity'), f.get('severity'))}{conf} | {f.get('page') or '–'} | "
                   f"{('/' + f['role']) if f.get('role') else '–'} | {_esc(f.get('issue', ''))} | "
                   f"{_esc(details)[:220]} | {_esc(f.get('action') or '')[:160]} |")
    if len(findings) > limit:
        out.append(f"| | | | …and {len(findings) - limit} more in the JSON report | | |")
    out.append("")
    return out


def _link(label: str, ref: str | None) -> str:
    if not ref:
        return "–"
    if ref.startswith(("http", "/")):
        return f"[{label}]({ref})"
    return f"{label} (base64, {len(ref) * 3 // 4:,} bytes)"


def render_job_markdown(job_id: str, meta: dict, status: str, result: dict | None = None,
                        error: dict | None = None, progress: dict | None = None,
                        ref: FileRef = lambda n: n) -> str:
    name = meta.get("filename") or "document.pdf"
    out = [f"# Accessibility remediation report: {_esc(name)}", ""]
    out += [f"- **Job:** `{job_id}`", f"- **Status:** {status}",
            "- **AI provider:** none: made without AI" if (meta.get("ai_provider") or "none") == "none"
            else f"- **AI provider:** {meta.get('ai_provider')}" + (f" (`{meta['ai_model']}`)" if meta.get("ai_model") else "")]
    flags = {k: v for k, v in (meta.get("flags") or {}).items() if v}
    if flags:
        out.append("- **Flags:** " + ", ".join(f"`{k}={v}`" for k, v in flags.items()))
    out.append("")

    if status == "failed":
        out += ["## Error", "", f"**{_esc((error or {}).get('error'))}**: {_esc((error or {}).get('detail'))}", ""]
        return "\n".join(out)
    if status != "completed" or result is None:
        stage = (progress or {}).get("stage", "queued")
        pct = (progress or {}).get("percent")
        out += [f"Processing: stage **{stage}**" + (f" ({pct}%)" if pct is not None else "") + ".", ""]
        return "\n".join(out)

    reports = result.get("reports") or {}
    comp = reports.get("comparison") or {}
    before, after = comp.get("before") or {}, comp.get("after")
    files = result.get("files") or {}

    # --- verdict ------------------------------------------------------------------------
    out += ["## Result", ""]
    if after is None:
        out.append("Report only: no changes were applied.")
    else:
        verdict = "passes" if after.get("compliant") else "does not yet pass" if after.get("compliant") is False else "was not validated against"
        out.append(f"The remediated document **{verdict} PDF/UA-{2 if after.get('pdfua_part') == 2 else 1}**"
                   + (f" ({before.get('failed_checks')} → {after.get('failed_checks')} failed checks)."
                      if isinstance(before.get("failed_checks"), int) and isinstance(after.get("failed_checks"), int) else "."))
        if comp.get("pdfua2_changes"):
            from app.reports.pdf import pdf2_note
            out += ["", pdf2_note(comp)]
        if files.get("remediated_visual_fallback"):
            pages = ", ".join(str(p) for p in comp.get("destructive_pages") or [])
            out.append("")
            fb = comp.get("after_visual_fallback") or {}
            if after.get("compliant"):
                modified_state = "The *modified* file is compliant"
            else:
                modified_state = (f"The *modified* file has fewer failures ({after.get('failed_checks')} vs "
                                  f"{fb.get('failed_checks')} failed checks) but is not yet fully compliant")
            changes = []
            if comp.get("fonts_embedded"):
                changes.append(f"{len(comp['fonts_embedded'])} unembedded font(s) replaced by embedded "
                               "metric-compatible substitutes (text and tags unchanged)")
            if pages:
                changes.append(f"page(s) {pages} flattened to an image with an invisible tagged text layer")
            out.append(f"> **Two files returned.** The *modified* file has: {'; '.join(changes) or 'visual changes'}. "
                       f"{modified_state}; the *visual* file looks exactly like the original.")
    out.append("")

    out += _review_section(reports.get("tag_verification"))

    # --- files ---------------------------------------------------------------------------
    after_xml = reports.get("verapdf_after") or {}
    out += ["## Files", "", "| File | Link |", "|---|---|"]
    if files.get("remediated_primary"):
        label = "Compliant (modified)" if files.get("remediated_visual_fallback") else "Remediated PDF"
        out.append(f"| {label} | {_link(files['remediated_primary'], ref(files['remediated_primary']))} |")
    if files.get("remediated_visual_fallback"):
        out.append(f"| Visually exact | {_link(files['remediated_visual_fallback'], ref(files['remediated_visual_fallback']))} |")
    if reports.get("verapdf_before"):
        out.append(f"| veraPDF report: before | {_link(reports['verapdf_before'], ref(reports['verapdf_before']))} |")
    if after_xml.get("primary"):
        out.append(f"| veraPDF report: after | {_link(after_xml['primary'], ref(after_xml['primary']))} |")
    if after_xml.get("visual_fallback"):
        out.append(f"| veraPDF report: after (visual) | {_link(after_xml['visual_fallback'], ref(after_xml['visual_fallback']))} |")
    if result.get("full_report"):
        out.append(f"| Full JSON report | {_link(result['full_report'], ref(result['full_report']))} |")
    out.append("")

    # --- before / after table ---------------------------------------------------------------
    out += ["## Before and after", ""]
    fallback = comp.get("after_visual_fallback")
    header = "| Check | Before | After |" + (" After (visual) |" if fallback else "")
    out += [header, "|---|---|---|" + ("---|" if fallback else "")]
    for label, key in _ROWS:
        if key not in before and (after is None or key not in after):
            continue
        row = f"| {label} | {_fmt(key, before.get(key))} | {_fmt(key, after.get(key)) if after else '–'} |"
        if fallback:
            row += f" {_fmt(key, fallback.get(key))} |"
        out.append(row)
    vf = comp.get("visual_fidelity")
    if vf:
        out += ["", f"**Visual fidelity** (non-destructive output): {vf.get('pages_identical')}/{vf.get('pages_compared')} "
                    "pages pixel-identical to the original."]
    out.append("")

    # --- validation flavours ---------------------------------------------------------------
    snap = after if after is not None else before
    flavours = snap.get("verapdf") or {}
    if flavours:
        out += ["## veraPDF profiles" + (" (after)" if after is not None else ""), "",
                "| Profile | Verdict | Failed rules | Failed checks |", "|---|---|---|---|"]
        for fl, v in flavours.items():
            if "error" in v:
                out.append(f"| {fl} | unavailable | – | – |")
            else:
                out.append(f"| {fl} | {_fmt('compliant', v.get('compliant'))} | {v.get('failed_rules')} | {v.get('failed_checks')} |")
        out.append("")
        for fl, v in flavours.items():
            fails = v.get("failures") or []
            if not fails:
                continue
            out += [f"### Remaining {fl} failures", "", "| Clause | Checks | Pages | Requirement |", "|---|---|---|---|"]
            for f in fails:
                pages = ", ".join(str(p) for p in f.get("pages") or []) or "–"
                out.append(f"| {f['clause']}-{f['test']} | {f['failed_checks']} | {pages} | {_esc(f['description'])[:220]} |")
            out.append("")

    # --- existing tags ------------------------------------------------------------------------
    et = comp.get("existing_tags") or {}
    if et.get("present"):
        out += ["## Existing tags", ""]
        if et.get("rebuilt_tree"):
            out.append(f"The existing structure tree was discarded and rebuilt: {_esc(et.get('reason'))}.")
        else:
            out.append(f"| Retained | Repaired | Replaced | Removed (empty) | Made artifact | Unresolved |\n"
                       f"|---|---|---|---|---|---|\n"
                       f"| {et.get('retained', 0)} | {et.get('repaired', 0)} | {et.get('replaced', 0)} | "
                       f"{et.get('removed', 0)} | {et.get('artifacted', 0)} | {et.get('unresolved', 0)} |")
        samples = [s for s in et.get("samples") or [] if s.get("reasons")]
        if samples:
            out += ["", "| Element | Decision | Reason |", "|---|---|---|"]
            for s in samples[:25]:
                out.append(f"| /{_esc(s['role'])} | {s['decision']} | {_esc('; '.join(s['reasons']))} |")
        for f in et.get("findings") or []:
            out.append(f"- {_esc(f)}")
        out.append("")

    # --- AI audit (report-only) ------------------------------------------------------------------
    audit = comp.get("ai_audit")
    if audit:
        figs = audit.get("figures") or []
        if figs:
            out += ["## Figures (AI audit)", "", "| Page | Decorative | Suggested alt text |", "|---|---|---|"]
            for f in figs:
                out.append(f"| {f['page']} | {_fmt('', f['decorative'])} | {_esc(f.get('suggested_alt') or '–')} |")
            out.append("")
        tabs = audit.get("tables") or []
        if tabs:
            out += ["## Tables (AI audit)", "", "| Page | Size | Header rows | Header cols | Summary |", "|---|---|---|---|---|"]
            for t in tabs:
                out.append(f"| {t['page']} | {t['rows']}×{t['cols']} | {t['header_rows']} | {t['header_cols']} | {_esc(t.get('summary') or '–')} |")
            out.append("")

    # --- summary, findings, metrics -----------------------------------------------------------------
    summary = reports.get("remediation_summary")
    if summary:
        out += ["## Summary", ""] + [f"- {_esc(line.strip())}" for line in summary.splitlines() if line.strip()] + [""]
    findings = reports.get("ai_findings") or []
    warnings = result.get("warnings") or []
    if findings or warnings:
        out += ["## Findings and warnings", ""]
        out += [f"- ⚠️ {_esc(w)}" for w in warnings] + [f"- {_esc(f)}" for f in findings] + [""]

    m = result.get("metrics") or {}
    hits = [x for x in m.get("limits_hit") or [] if isinstance(x, dict)]
    if hits:
        from app.pipeline.limits import describe
        out += ["## Limits reached", "", "| Setting | Set to | This document | What happened |", "|---|---|---|---|"]
        out += [f"| {_esc(x.get('setting'))} | {x.get('value')} | {x.get('wanted')}{' pages' if x.get('count') else ''} | "
                f"{_esc(describe(x, with_setting=False))} |" for x in hits] + [""]
    tok = m.get("ai_tokens_used") or {}
    out += ["## Metrics", "", "| Metric | Value |", "|---|---|",
            f"| Pages processed | {m.get('pages_processed')} |",
            f"| Processing time | {m.get('processing_time_seconds')} s |",
            f"| AI calls | {m.get('ai_calls', 0)} |",
            f"| AI tokens (prompt / completion) | {tok.get('prompt_tokens', 0):,} / {tok.get('completion_tokens', 0):,} |"]
    for key, label in (("images_described", "Images described"), ("tables_analyzed", "Tables analyzed"),
                       ("reading_order_pages_ai", "Pages with AI reading order"), ("ocr_pages", "Pages OCR'd"),
                       ("scan_figures", "Figures found on scanned pages")):
        if m.get(key):
            out.append(f"| {label} | {m[key]} |")
    out.append("")
    rows = stage_rows(m)
    if rows:
        out += ["### Time by stage", "", "| Stage | Seconds |", "|---|---|"]
        out += [f"| {label} | {seconds:,.1f} |" for label, seconds in rows]
        out += ["", "Writing the reports themselves isn't counted.", ""]
    return "\n".join(out)


_STAGES = {
    "validating_original": "Checking the original (PDF/UA validation)",
    "analyzing": "Reading the layout (and OCR)",
    "ai_ocr": "AI correction of OCR",
    "ai_enrichment": "Music, then AI: alt text, tables, reading order, notation",
    "tagging": "Tagging",
    "ai_existing_figures": "AI: figures in existing tags",
    "validating_output": "Checking the result (PDF/UA validation, font fixes)",
    "destructive_fixes": "Font substitution",
    "flattening": "Flattening pages and tagging them again",
    "reporting": "Reports",
    "verifying_tags": "Verifying the tags (and AI review)",
}
_PARTS = {"validator": "waiting for the validator", "music": "finding and reading music"}


def stage_rows(metrics: dict) -> list[tuple[str, float]]:
    """A job's time by stage, in the order the stages ran, as (name for a reader, seconds); then the time inside
    those stages spent on particular things ("of which: ...")."""
    recorded = metrics.get("stage_seconds") or {}
    rows = [(_STAGES.get(k, k.replace("_", " ").capitalize()), float(v)) for k, v in recorded.items()
            if k != "of_which" and float(v) >= 0.05]  # a stage that took no time says nothing
    return rows + [(f"of which: {_PARTS.get(k, k)}", float(v)) for k, v in (recorded.get("of_which") or {}).items()
                   if float(v) >= 0.05]
