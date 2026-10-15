"""Where a document's time goes is recorded with the job, so a slow one explains itself."""
import asyncio
import uuid

from app.pipeline import orchestrator
from app.pipeline.orchestrator import JobOptions, StageClock, run_remediation
from app.reports.markdown import render_job_markdown
from app.storage import JobStore


def test_the_clock_adds_up_each_stage_and_the_parts_inside_them(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(orchestrator.time, "monotonic", lambda: now[0])
    seen = []
    clock = StageClock(lambda stage, pct: seen.append((stage, pct)))
    clock("analyzing", 15)
    now[0] += 4
    clock("tagging", 55)
    now[0] += 2.5
    clock.add("validator", 1.2)
    clock("analyzing", 60)      # a stage visited twice adds up
    now[0] += 1
    clock.add("validator", 0.3)
    assert clock.seconds() == {"analyzing": 5.0, "tagging": 2.5, "of_which": {"validator": 1.5}}
    assert seen == [("analyzing", 15), ("tagging", 55), ("analyzing", 60)]  # progress is still reported


def test_a_run_records_its_stages(samples, tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator.get_settings(), "allow_mock_provider", True, raising=False)
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", job_id=jid, provider="mock")))
    stages = result["metrics"]["stage_seconds"]
    assert list(stages)[:2] == ["validating_original", "analyzing"] and "tagging" in stages
    assert all(v >= 0 for k, v in stages.items() if k != "of_which")
    assert sum(v for k, v in stages.items() if k != "of_which") <= result["metrics"]["processing_time_seconds"] + 0.5
    text = render_job_markdown(jid, {}, "completed", result)
    assert "### Time by stage" in text and "| Verifying the tags (and AI review) |" in text
    assert "| 0.0 |" not in text  # a stage that took no time on this small document isn't listed


def test_the_report_pdf_has_the_table_too():
    from app.reports.pdf import build_html

    result = {"metrics": {"pages_processed": 3, "processing_time_seconds": 12.0,
                          "stage_seconds": {"analyzing": 4.0, "tagging": 2.5, "verifying_tags": 5.0,
                                            "of_which": {"validator": 1.5}}},
              "reports": {}, "files": {}, "warnings": []}
    html = build_html({"filename": "doc.pdf"}, result)
    assert "Time by stage" in html and "Reading the layout (and OCR)" in html
    assert "of which: waiting for the validator" in html and ">1.5<" in html
