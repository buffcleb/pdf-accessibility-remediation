"""The engine's version (the image's tag, set by scripts/build.sh) is recorded with every result, so a document's Run
details say which engine made it."""
import uuid

from app.config import get_settings
from app.storage import JobStore


def test_the_result_records_the_engine_version(tmp_path, monkeypatch):
    store = JobStore(str(uuid.uuid4()), root=tmp_path)
    store.create()
    monkeypatch.setattr(get_settings(), "engine_version", "7278730a36")
    store.write_result({"metrics": {"route": "standard"}})
    assert store.read_result()["metrics"] == {"route": "standard", "engine_version": "7278730a36"}
    store.write_result({"status": "saved"})           # a result without metrics (a saved version) gets them
    assert store.read_result()["metrics"] == {"engine_version": "7278730a36"}


def test_without_a_version_nothing_is_added(tmp_path, monkeypatch):
    store = JobStore(str(uuid.uuid4()), root=tmp_path)
    store.create()
    monkeypatch.setattr(get_settings(), "engine_version", "")
    store.write_result({"status": "done"})
    assert store.read_result() == {"status": "done"}
