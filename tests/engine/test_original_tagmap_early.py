"""The viewer's tag map of the original is built on its own thread from the start of a job, so it runs while the
job waits on the validator and the AI, instead of being built at the end after everything else."""
import asyncio
import time

from app.pipeline import orchestrator
from app.pipeline.orchestrator import JobOptions, run_remediation
from app.storage import JobStore


def test_the_originals_tag_map_is_built_while_the_original_is_validated(samples, tmp_path, monkeypatch):
    moments: dict[str, float] = {}
    original_validate, original_build = orchestrator._validate, orchestrator.build_tagmap

    def slow_validate(*args, **kwargs):
        # The validator is a subprocess the job waits on; stand in for it with a wait the map can run during
        time.sleep(1.0)
        moments["validated"] = time.monotonic()
        return original_validate(*args, **kwargs)

    def build(path):
        moments.setdefault("map_started", time.monotonic())
        return original_build(path)

    monkeypatch.setattr(orchestrator, "_validate", slow_validate)
    monkeypatch.setattr(orchestrator, "build_tagmap", build)
    store = JobStore("00000000-0000-4000-8000-000000000002", root=tmp_path)
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    assert result["reports"]["tag_maps"]["original"] == "doc_tagmap_original.json"
    assert (store.output_dir / "doc_tagmap_original.json").exists()
    assert moments["map_started"] < moments["validated"], "the original's tag map waited for the validator"
