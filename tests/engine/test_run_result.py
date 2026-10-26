"""The run's report keeps the keys the report readers (app/reports, the portal) read (refactor plan item 7)."""
import asyncio

from app.pipeline.orchestrator import JobOptions, run_remediation
from app.pipeline.run_result import RunResult, ai_usage
from app.storage import JobStore

# A whole run's keys (with the mock provider), as main wrote them before the result was built in one place
TOP = ["files", "full_report", "markdown_report", "metrics", "report_pdf", "report_pdf_compliant", "reports", "warnings"]
METRICS = ["ai_calls", "ai_cost_usd", "ai_failures", "ai_model", "ai_provider", "ai_tokens_used", "images_described",
           "limits_hit", "music_examples", "music_read", "notation_ai_rejected", "notation_ai_rejected_by_reason",
           "notation_by_rule", "notation_read_by_ai", "ocr_ai_lines_transcribed", "ocr_ai_rejected",
           "ocr_ai_words_corrected", "ocr_pages", "pages_processed", "processing_time_seconds",
           "reading_order_pages_ai", "scan_figures", "stage_seconds", "tables_analyzed",
           "verification_ai_cost_usd", "verification_ai_tokens"]  # the last two only when the AI reviewed the tags
REPORTS = ["ai_findings", "ai_notation", "ai_ocr", "comparison", "math", "math_disagreements", "math_files", "math_list",
           "math_nemeth", "math_ueb", "music", "music_files", "music_score", "remediation_summary", "tag_maps",
           "tag_verification", "tagging", "verapdf_after", "verapdf_before"]


def test_a_whole_run_has_the_keys_the_readers_read(samples, tmp_path):
    store = JobStore("00000000-0000-4000-8000-000000000007", root=tmp_path)
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    # The report PDF is there only where WeasyPrint can write it
    assert sorted(set(result) | {"report_pdf", "report_pdf_compliant"}) == TOP
    assert sorted(result["metrics"]) == METRICS
    assert sorted(result["reports"]) == REPORTS


def test_the_result_is_the_four_parts_in_order():
    r = RunResult(files={}, metrics={"a": 1}, reports={}, warnings=[])
    assert list(r.to_dict()) == ["files", "metrics", "reports", "warnings"]
    assert r.to_dict()["metrics"] is r.metrics  # filled in later, as the run goes


def test_without_an_ai_nothing_was_used():
    assert ai_usage(None) == {"ai_tokens_used": {"prompt_tokens": 0, "completion_tokens": 0}, "ai_cost_usd": 0.0,
                              "ai_calls": 0, "ai_failures": 0}
