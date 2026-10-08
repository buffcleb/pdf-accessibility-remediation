"""The report PDF: built from the structured result, accessible itself (PDF/UA-1), never naming models."""
from __future__ import annotations

import shutil

import pytest

from app.reports.pdf import build_html, write_report_pdf

RESULT = {
    "files": {"remediated_primary": "doc_remediated.pdf", "remediated_visual_fallback": "doc_visual.pdf"},
    "metrics": {"pages_processed": 42, "processing_time_seconds": 192.4, "ai_model": "secret-model-name",
                "images_described": 22},
    "reports": {
        "remediation_summary": "Tagged 14 headings.\nWrote alt text for 22 figures.",
        "comparison": {
            "destructive_pages": [3, 17],
            "before": {"compliant": False, "failed_rules": 3, "failed_checks": 120, "headings": 0, "figures": 22,
                       "figures_missing_alt": 22, "language": None,
                       "verapdf": {"ua1": {"failed_rules": 3, "failures": [
                           {"clause": "7.1", "test": "3", "description": "Content is tagged or marked as artifact",
                            "failed_checks": 84, "pages": [1, 2]},
                           {"clause": "7.3", "test": "1", "description": "Figures have alternative text",
                            "failed_checks": 22, "pages": [3]},
                           {"clause": "7.2", "test": "34", "description": "Tables <have> header cells",
                            "failed_checks": 14, "pages": [5]}]}}},
            "after": {"compliant": False, "failed_rules": 1, "failed_checks": 2, "headings": 14, "figures": 22,
                      "figures_missing_alt": 0, "language": "en-US",
                      "verapdf": {"ua1": {"failed_rules": 1, "failures": [
                          {"clause": "7.2", "test": "34", "description": "Tables <have> header cells",
                           "failed_checks": 2, "pages": [5, 9]}]}}},
        },
        "tag_verification": {"primary": {"findings": [
            {"severity": "high", "page": 3, "issue": "Alt text may be wrong", "text": "Enrollment by year",
             "suggested_alt": "Line graph of enrollment, 2015 to 2025"},
            {"severity": "suggest", "page": 9, "issue": "Logo could be decorative"}]}},
    },
    "warnings": ["Page 12 was scanned."],
}
META = {"filename": "Annual report <2025>.pdf", "flags": {}}
BRAND = {"service_name": "Campus Accessibility", "organization": "Example University", "accent_color": "#7a1f3d",
         "ai_label": "Premium AI · Math and science"}


def test_the_report_reads_as_a_report():
    h = build_html(META, RESULT, BRAND)
    assert '<html lang="en">' in h and "<title>Accessibility report: Annual report &lt;2025&gt;.pdf</title>" in h
    assert h.count("<h1>") == 1 and "Campus Accessibility · Example University" in h and "#7a1f3d" in h
    assert "does not yet fully pass" in h and "failed checks went from 120 to 2" in h
    assert "pages 3 and 17" in h and "(1 rule failed)" in h  # two files: where it looks different
    # The check, rule by rule, before and after, with the rule still failing first
    i_tables, i_content = h.index("7.2-34"), h.index("7.1-3")
    assert i_tables < i_content and 'Failed after</th>' in h
    assert "Tables &lt;have&gt; header cells" in h and "5, 9" in h
    assert "<h2>Still failing</h2>" in h and "<h2>For review</h2>" in h
    assert "Suggested alt text: “Line graph of enrollment, 2015 to 2025”" in h
    assert "Premium AI · Math and science" in h and "secret-model-name" not in h  # never model names
    assert '<th scope="row">' in h and "<caption>" in h


def test_an_audit_has_no_after():
    result = {**RESULT, "files": {}, "reports": {**RESULT["reports"], "comparison": {
        "before": RESULT["reports"]["comparison"]["before"]}}}
    h = build_html(META, result, None)
    assert "<h1>Accessibility audit</h1>" in h and "nothing was changed" in h
    assert "Failed after" not in h and "<h2>Still failing</h2>" not in h
    assert "#1f4e8c" in h  # the default accent when none is given
    assert "#zzz" not in build_html(META, result, {"accent_color": "#zzz"})  # only real colors


@pytest.mark.skipif(shutil.which("java") is None, reason="needs veraPDF (Java)")
def test_the_pdf_passes_pdf_ua_1(tmp_path):
    pytest.importorskip("weasyprint")
    out = tmp_path / "report.pdf"
    check = write_report_pdf(out, META, RESULT, BRAND)
    assert out.exists() and out.read_bytes().startswith(b"%PDF")
    if check["error"] and "veraPDF" in check["error"]:
        pytest.skip(check["error"])
    assert check["compliant"] is True, check


def test_tables_across_a_page_break_still_make_a_pdf():
    """Rows are kept whole, so a table can start at the very bottom of a page; WeasyPrint's PDF/UA tagging failed
    when its caption was left there alone ("Table wrapper without a table"). Shift the tables through the page
    break and every report must still be written."""
    weasyprint = pytest.importorskip("weasyprint")
    for n in range(0, 40):
        result = {**RESULT, "reports": {**RESULT["reports"],
                                        "remediation_summary": "\n".join(f"Step {i} done." for i in range(n))}}
        weasyprint.HTML(string=build_html(META, result, BRAND)).write_pdf(pdf_variant="pdf/ua-1")
