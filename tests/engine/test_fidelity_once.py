"""The pixel comparison of the visual output renders every page of both files. When glyph widths were corrected
it ran once to judge the corrections and again for the report, on the same file: now the first answer is kept."""
import asyncio

from app.pipeline import orchestrator
from app.pipeline.orchestrator import JobOptions, run_remediation
from app.storage import JobStore


def _run(samples, tmp_path, monkeypatch, widths_fixed: int) -> int:
    """How many times the comparison ran for one job, with the width fixer made to report `widths_fixed`."""
    calls = []
    original = orchestrator.visual_fidelity
    monkeypatch.setattr(orchestrator, "visual_fidelity", lambda a, b, dpi=50: calls.append(1) or original(a, b, dpi))
    monkeypatch.setattr(orchestrator, "_fix_widths", lambda pdf, vera, work, label, stem: (vera, widths_fixed))
    store = JobStore("00000000-0000-4000-8000-000000000001", root=tmp_path)
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    assert result["reports"]["comparison"]["visual_fidelity"]["identical"]
    return len(calls)


def test_the_comparison_runs_once_when_widths_were_corrected(samples, tmp_path, monkeypatch):
    assert _run(samples, tmp_path, monkeypatch, widths_fixed=3) == 1


def test_the_comparison_runs_once_when_nothing_was_corrected(samples, tmp_path, monkeypatch):
    assert _run(samples, tmp_path, monkeypatch, widths_fixed=0) == 1
