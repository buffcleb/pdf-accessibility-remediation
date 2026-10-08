"""File backup and restore through the portal's storage (scripts/backup.sh and restore.sh use these)."""
from __future__ import annotations

import io
import tarfile

from portal.backup_tools import export_files, import_files
from portal.cli import main
from portal.storage import get_storage


def _seed():
    st = get_storage()
    st.objects.clear()
    st.put_bytes("groups/a/users/u/jobs/j/input/doc.pdf", b"%PDF-1.7 original", "application/pdf")
    st.put_bytes("groups/a/users/u/jobs/j/reports/doc_report.md", b"# Report", "text/markdown; charset=utf-8")
    st.put_bytes("groups/b/users/v/jobs/k/output/x.pdf", b"%PDF-1.7 other group", "application/pdf")
    return st


def test_export_then_restore_everything():
    st = _seed()
    buf = io.BytesIO()
    assert export_files(buf, "") == (3, sum(len(v[0]) for v in st.objects.values()))
    saved = dict(st.objects)
    st.objects.clear()
    buf.seek(0)
    stats = import_files(buf)
    assert stats["restored"] == 3
    assert st.objects == saved  # same bytes and content types


def test_restore_keeps_existing_unless_overwrite_and_dry_run_changes_nothing():
    st = _seed()
    buf = io.BytesIO()
    export_files(buf, "groups/a/")
    st.put_bytes("groups/a/users/u/jobs/j/input/doc.pdf", b"newer", "application/pdf")
    del st.objects["groups/a/users/u/jobs/j/reports/doc_report.md"]

    buf.seek(0)
    assert import_files(buf, dry_run=True)["restored"] == 1
    assert "groups/a/users/u/jobs/j/reports/doc_report.md" not in st.objects
    buf.seek(0)
    stats = import_files(buf)
    assert stats == {"restored": 1, "skipped_existing": 1, "skipped_other": 0, "bytes": len(b"# Report")}
    assert st.objects["groups/a/users/u/jobs/j/input/doc.pdf"][0] == b"newer"
    buf.seek(0)
    import_files(buf, overwrite=True)
    assert st.objects["groups/a/users/u/jobs/j/input/doc.pdf"][0] == b"%PDF-1.7 original"


def test_unsafe_names_are_never_restored():
    st = get_storage()
    st.objects.clear()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in ("../escape.pdf", "/abs.pdf", "ok/file.pdf"):
            info = tarfile.TarInfo(name)
            info.size = 3
            tar.addfile(info, io.BytesIO(b"pdf"))
    buf.seek(0)
    stats = import_files(buf)
    assert stats["restored"] == 1 and stats["skipped_other"] == 2
    assert list(st.objects) == ["ok/file.pdf"]


def test_cli_streams(monkeypatch, capsysbinary):
    _seed()

    class Out:
        buffer = io.BytesIO()

    monkeypatch.setattr("sys.stdout", Out)
    assert main(["export-files", "--prefix", "groups/b/"]) == 0
    with tarfile.open(fileobj=io.BytesIO(Out.buffer.getvalue())) as tar:
        assert tar.getnames() == ["groups/b/users/v/jobs/k/output/x.pdf"]
    assert main([]) == 2
