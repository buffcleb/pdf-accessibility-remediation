"""The remediation report as an accessible PDF, for people (the Markdown and JSON reports stay for tools).

Rendered from the job's structured result as semantic HTML (one h1, h2 per section, tables with header cells and
captions, the document language and title), then written as tagged PDF/UA-1 by WeasyPrint, and checked with
veraPDF like any other output. Includes the PDF/UA-1 check results, rule by rule, before and after.

Branding (service and organization name, accent color) comes from the job's `report_branding` flag. Model names
never appear: the report is for the person who sent the document.
"""
from __future__ import annotations

import html
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.reports.markdown import stage_rows

log = logging.getLogger(__name__)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
REVIEW_LIMIT = 150

_ROWS = [
    ("Headings", "headings"),
    ("Heading level skips", "heading_level_skips"),
    ("Figures", "figures"),
    ("Figures missing alt text", "figures_missing_alt"),
    ("Tables", "tables"),
    ("Tables without header cells", "tables_without_headers"),
    ("Content tagged or marked as decoration", "content_tagged_ratio"),
    ("Untagged annotations", "annotations_untagged"),
    ("Font problems", "font_problems"),
    ("Language", "language"),
    ("Title", "title"),
]
_SEVERITY = {"high": "High", "review": "Review", "suggest": "Suggestion", "warning": "Note"}


# Private use characters: what one stands for depends on the font it came from (a Wingdings arrow U+F0E0, a Symbol
# bullet U+F0B7, a music font's notes), and the report quotes the document's text without its fonts. No font in the
# report has them, so each would be drawn as .notdef, which PDF/UA-1 forbids (7.21.8, #600)
_PRIVATE_USE = re.compile("[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def drawable(page: str) -> str:
    """The report's HTML with each private use character the document's text brought in shown as a white square (□,
    U+25A1). Not U+FFFD: the report's font has it, but the PDF WeasyPrint writes maps it back to another character."""
    return _PRIVATE_USE.sub("\u25a1", page)


def pdf2_note(comp: dict) -> str:
    """What a report says about a file converted to PDF 2.0 for PDF/UA-2 (#445)."""
    caution = "Some older PDF readers and checkers don't support PDF 2.0 yet."
    copy = comp.get("after_pdfua2")
    if copy:  # Both: a PDF/UA-2 copy beside the PDF/UA-1 file
        verdict = "passes" if copy.get("compliant") else "does not yet fully pass"
        return f"A second file was made from it as PDF 2.0, for PDF/UA-2: it {verdict} the PDF/UA-2 check. {caution}"
    return f"The file was converted to PDF 2.0, as PDF/UA-2 needs. {caution}"


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _val(key: str, v) -> str:
    if v is None or v == "":
        return "–"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if key == "content_tagged_ratio" and isinstance(v, (int, float)):
        return f"{v:.0%}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _verdict(snap: dict | None) -> str:
    if not snap:
        return "–"
    return "Passes" if snap.get("compliant") else "Fails" if snap.get("compliant") is False else "Not checked"


def _table(caption: str, head: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    th = "".join(f'<th scope="col"{" class=\"num\"" if i in numeric else ""}>{_e(h)}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(
        (f'<th scope="row">{c}</th>' if i == 0 else f'<td{" class=\"num\"" if i in numeric else ""}>{c}</td>')
        for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f"<table><caption>{_e(caption)}</caption><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _and(items) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _part(snap: dict | None) -> int:
    """Which PDF/UA the verdict is by: 2 for a PDF 2.0 file, else 1 (#409)."""
    return 2 if (snap or {}).get("pdfua_part") == 2 else 1


def _ua(snap: dict | None) -> dict:
    """The check the verdict is by, rule by rule."""
    return ((snap or {}).get("verapdf") or {}).get(f"ua{_part(snap)}") or {}


def build_html(meta: dict, result: dict, branding: dict | None = None) -> str:
    """The report as one self-contained HTML page (also used for tests)."""
    b = branding or {}
    service = b.get("service_name") or "PDF accessibility remediation"
    org = b.get("organization") or ""
    accent = b.get("accent_color") if _HEX.match(str(b.get("accent_color") or "")) else "#1f4e8c"
    level = b.get("ai_label")
    name = meta.get("filename") or "document.pdf"
    reports = result.get("reports") or {}
    comp = reports.get("comparison") or {}
    before, after = comp.get("before") or {}, comp.get("after")
    audit_only = after is None
    metrics = result.get("metrics") or {}
    files = result.get("files") or {}
    title = f"{'Accessibility audit' if audit_only else 'Accessibility report'}: {name}"
    when = datetime.now(timezone.utc).strftime("%-d %B %Y")
    pages = metrics.get("pages_processed")

    s: list[str] = []
    s.append(f'<header><p class="brand">{_e(service)}{f" · {_e(org)}" if org else ""}</p>'
             f"<h1>{_e('Accessibility audit' if audit_only else 'Accessibility report')}</h1>"
             f'<p class="facts">{_e(name)}{f" · {pages:,} pages" if isinstance(pages, int) else ""}'
             f"{f' · {_e(level)}' if level else ''} · {when}</p></header>")

    # --- summary --------------------------------------------------------------------------------------
    s.append("<h2>Summary</h2>")
    fb, fa = before.get("failed_checks"), (after or {}).get("failed_checks")
    if audit_only:
        s.append(f'<p class="verdict">The document <strong>{"passes" if before.get("compliant") else "does not pass"}'
                 f"</strong> the PDF/UA-{_part(before)} accessibility check"
                 f"{f' ({fb:,} failed checks)' if isinstance(fb, int) else ''}. "
                 "It was checked only; nothing was changed.</p>")
    else:
        verdict = "passes" if after.get("compliant") else "does not yet fully pass"
        s.append(f'<p class="verdict">The remediated document <strong>{verdict}</strong> the PDF/UA-{_part(after)} '
                 'accessibility check'
                 + (f": failed checks went from {fb:,} to {fa:,}." if isinstance(fb, int) and isinstance(fa, int) else ".")
                 + "</p>")
        if comp.get("pdfua2_changes"):
            s.append(f"<p>{_e(pdf2_note(comp))}</p>")
        if files.get("remediated_visual_fallback"):
            dp = comp.get("destructive_pages") or []
            s.append("<p>Two files were returned: the <strong>remediated PDF</strong>, which passes where possible but looks "
                     "slightly different" + (f" on page{'s' if len(dp) > 1 else ''} {_and(dp)}" if dp else "")
                     + ", and a <strong>visually exact PDF</strong>, which looks identical to the original.</p>")
        if metrics.get("ai_provider") == "none":  # #659: No AI
            missing = after.get("figures_missing_alt")
            s.append("<p>Made <strong>without AI</strong>"
                     + (f": {missing:,} figure{'s have' if missing != 1 else ' has'} no alt text, each listed under "
                        "<em>For review</em> for a person to describe." if isinstance(missing, int) and missing
                        else ".") + "</p>")
    tv = (reports.get("tag_verification") or {})
    primary = tv.get("primary") or tv.get("original") or next((v for v in tv.values() if v), None) or {}
    findings = primary.get("findings") or []
    if findings:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.get("severity", "")] = counts.get(f.get("severity", ""), 0) + 1
        parts = [f"{counts[k]:,} {_SEVERITY[k].lower()}" for k in ("high", "review", "suggest") if counts.get(k)]
        s.append(f"<p>{len(findings):,} item{'s' if len(findings) != 1 else ''} for a person to check"
                 + (f" ({', '.join(parts)})" if parts else "") + ". See <em>For review</em>.</p>")
    lines = [ln.strip() for ln in (reports.get("remediation_summary") or "").splitlines() if ln.strip()]
    if lines:
        s.append("<ul>" + "".join(f"<li>{_e(ln)}</li>" for ln in lines[:12]) + "</ul>")

    # --- what changed ------------------------------------------------------------------------------------
    if not audit_only:
        rows = [[_e(label), _e(_val(k, before.get(k))), _e(_val(k, after.get(k)))]
                for label, k in _ROWS if k in before or k in after]
        if rows:
            s.append("<h2>What changed</h2>" + _table("Before and after remediation", ["", "Before", "After"], rows, {1, 2}))

    # --- the PDF/UA check, rule by rule (PDF/UA-2 for a PDF 2.0 file) -----------------------------------
    ub, ua = _ua(before), _ua(after) if after else {}
    s.append(f"<h2>Accessibility check (PDF/UA-{_part(after or before)})</h2>")
    def _rules(n) -> str:
        n = n or 0
        return f"{n} rule{'s' if n != 1 else ''} failed"
    s.append(f"<p>Checked with veraPDF. Before: <strong>{_verdict(before)}</strong>"
             + (f" ({_rules(ub.get('failed_rules', before.get('failed_rules')))})" if ub or before else "")
             + (f". After: <strong>{_verdict(after)}</strong> ({_rules((ua or {}).get('failed_rules', after.get('failed_rules')))})."
                if after else ".") + "</p>")
    rules: dict[str, dict] = {}
    for when_key, snap in (("before", ub), ("after", ua)):
        for f in (snap or {}).get("failures") or []:
            key = f"{f.get('clause')}-{f.get('test')}" if f.get("test") is not None else str(f.get("clause"))
            r = rules.setdefault(key, {"description": f.get("description") or "", "before": 0, "after": 0, "pages": []})
            r[when_key] = f.get("failed_checks") or 0
            if when_key == "after":
                r["pages"] = f.get("pages") or []
    if rules:
        head = ["Rule", "What it checks", "Failed before"] + ([] if audit_only else ["Failed after", "Pages still failing"])
        rows = []
        for key, r in sorted(rules.items(), key=lambda kv: (-kv[1]["after"], -kv[1]["before"])):
            row = [_e(key), _e(r["description"]), _e(f"{r['before']:,}")]
            if not audit_only:
                pg = ", ".join(str(p) for p in r["pages"][:30]) + ("…" if len(r["pages"]) > 30 else "")
                row += [_e(f"{r['after']:,}"), _e(pg or "–")]
            rows.append(row)
        s.append(_table("Rules that failed, before and after", head, rows, {2, 3}))
    else:
        s.append("<p>No rule failed.</p>")

    # --- still failing ------------------------------------------------------------------------------------
    if not audit_only:
        s.append("<h2>Still failing</h2>")
        still = [(k, r) for k, r in rules.items() if r["after"]]
        if still:
            s.append("<p>These rules still fail after remediation. Most can be fixed in the portal's tag editor or need a "
                     "change to the source document.</p><ul>"
                     + "".join(f"<li><strong>{_e(k)}</strong>: {_e(r['description'])} ({r['after']:,} "
                               f"check{'s' if r['after'] != 1 else ''})</li>" for k, r in still) + "</ul>")
        else:
            s.append(f"<p>Nothing. Every PDF/UA-{_part(after)} rule passes.</p>")

    # --- for review ----------------------------------------------------------------------------------------
    s.append("<h2>For review</h2>")
    if not findings:
        s.append("<p>Nothing was flagged for a person to check.</p>")
    else:
        s.append("<p>Things a machine can't be sure about. Check the high and review items before publishing; AI-written "
                 "alt text is a draft.</p>")
        rows = []
        for f in findings[:REVIEW_LIMIT]:
            detail = f.get("suggested_alt") and f"Suggested alt text: “{f['suggested_alt']}”" or f.get("action") or f.get("detail") or ""
            what = f.get("issue") or ""
            if f.get("text"):
                what += f" (“{str(f['text'])[:120]}”)"
            rows.append([_e(_SEVERITY.get(f.get("severity"), f.get("severity") or "")), _e(f.get("page") or "–"),
                         _e(what), _e(str(detail)[:300])])
        s.append(_table("Items for a person to check", ["Severity", "Page", "Item", "What to do"], rows, {1}))
        if len(findings) > REVIEW_LIMIT:
            s.append(f"<p>And {len(findings) - REVIEW_LIMIT:,} more, listed in the portal.</p>")

    # --- details ----------------------------------------------------------------------------------------------
    facts = [("Pages", _val("", pages))]
    if metrics.get("processing_time_seconds") is not None:
        secs = float(metrics["processing_time_seconds"])
        facts.append(("Time taken", f"{int(secs // 60)} min {int(secs % 60)} s" if secs >= 60 else f"{secs:.0f} s"))
    if metrics.get("route") == "adobe":
        facts.append(("Tagged by", "Adobe PDF Services"))
    if level:
        facts.append(("AI", level))
    if metrics.get("ocr_pages"):
        facts.append(("Scanned pages read with OCR", _val("", metrics["ocr_pages"])))
    if metrics.get("scan_figures"):
        facts.append(("Figures found on scanned pages", _val("", metrics["scan_figures"])))
    if metrics.get("images_described"):
        facts.append(("Images described", _val("", metrics["images_described"])))
    s.append("<h2>Details</h2><dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in facts) + "</dl>")
    stages = stage_rows(metrics)
    if stages:
        s.append(_table("Time by stage, in seconds (writing this report isn't counted)", ["Stage", "Seconds"],
                        [[_e(label), f"{seconds:,.1f}"] for label, seconds in stages], {1}))
    warnings = result.get("warnings") or []
    if warnings:
        s.append("<h3>Notes</h3><ul>" + "".join(f"<li>{_e(w)}</li>" for w in warnings[:20]) + "</ul>")
    hits = [x for x in (result.get("metrics") or {}).get("limits_hit") or [] if isinstance(x, dict)]
    if hits:
        from app.pipeline.limits import describe
        # Limits cut this run short somewhere: said plainly, so a capped result isn't taken for a complete one
        s.append("<h3>Limits reached</h3><ul>" + "".join(f"<li>{_e(describe(x, with_setting=False))}</li>"
                                                        for x in hits) + "</ul>")

    css = f"""
    @page {{ size: A4; margin: 18mm 16mm 18mm 16mm;
      @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font-family: "DejaVu Sans", sans-serif;
        font-size: 8.5pt; color: #536074; }} }}
    body {{ font-family: "DejaVu Sans", sans-serif; font-size: 10pt; line-height: 1.45; color: #172030; }}
    header {{ border-bottom: 3px solid {accent}; padding-bottom: 8pt; margin-bottom: 12pt; }}
    .brand {{ margin: 0; font-size: 9pt; color: #536074; }}
    h1 {{ font-size: 19pt; margin: 4pt 0 2pt; }}
    .facts {{ margin: 0; color: #536074; }}
    h2 {{ font-size: 13pt; color: {accent}; margin: 16pt 0 6pt; break-after: avoid; }}
    h3 {{ font-size: 11pt; margin: 10pt 0 4pt; }}
    .verdict {{ font-size: 11pt; }}
    table {{ border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; font-size: 9pt; }}
    caption {{ text-align: left; font-weight: bold; padding-bottom: 4pt; break-after: avoid; }}
    th, td {{ border-bottom: 0.5pt solid #c9d0da; padding: 3pt 5pt; text-align: left; vertical-align: top; }}
    thead th {{ background: #eef1f5; }}
    tbody th {{ font-weight: normal; }}
    .num {{ text-align: right; }}
    tr {{ break-inside: avoid; }}
    /* With rows kept whole, a caption could be left at the bottom of a page with its table on the next, and
       WeasyPrint's PDF/UA tagging fails on that piece ("Table wrapper without a table"): keep them together */
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 2pt 12pt; }}
    dt {{ color: #536074; }} dd {{ margin: 0; }}
    """
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f'<meta name="author" content="{_e(service)}"><style>{css}</style></head><body>' + "".join(s) + "</body></html>")


def write_report_pdf(out_path: Path, meta: dict, result: dict, branding: dict | None = None) -> dict:
    """Write the report PDF and check it. Returns {"compliant": bool|None, "error": str|None}; never raises."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001 - optional: without WeasyPrint there's no PDF report, nothing else breaks
        log.warning("report PDF unavailable: %s", exc)
        return {"compliant": None, "error": "PDF reports aren't available on this engine"}
    try:
        HTML(string=drawable(build_html(meta, result, branding))).write_pdf(str(out_path), pdf_variant="pdf/ua-1")
    except Exception as exc:  # noqa: BLE001 - a report problem never fails the job
        log.exception("report PDF failed")
        return {"compliant": None, "error": f"couldn't write the report PDF: {exc}"[:300]}
    try:
        from app.validation.verapdf import run_verapdf
        with tempfile.TemporaryDirectory() as tmp:
            res = run_verapdf(out_path, Path(tmp) / "report_check.xml", "ua1")
        if res.error:
            return {"compliant": None, "error": res.error}
        if not res.compliant:
            log.warning("report PDF fails PDF/UA-1: %s", [f.clause for f in res.failures][:10])
        return {"compliant": res.compliant, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"compliant": None, "error": str(exc)[:300]}
