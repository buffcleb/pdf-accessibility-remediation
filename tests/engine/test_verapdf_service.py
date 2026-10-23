"""veraPDF's running service: used when configured, the command line when it doesn't answer or a job says so."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import get_settings
from app.validation import verapdf
from app.validation.verapdf import flavours, job_validator, run_verapdf

REPORT = b"""<?xml version="1.0" encoding="utf-8"?>
<report><jobs><job><validationReport profileName="PDF/UA-1 validation profile" isCompliant="false">
<details passedRules="100" failedRules="1" passedChecks="500" failedChecks="2">
<rule specification="ISO 14289-1:2014" clause="7.1" testNumber="3" status="failed" failedChecks="2">
<description>Content shall be marked as Artifact or tagged as real content</description>
<check status="failed"><context>root/document[0]/pages[1](12 0 obj PDPage)/contentStream[0]</context></check>
<check status="failed"><context>root/document[0]/pages[4](30 0 obj PDPage)/contentStream[0]</context></check>
</rule></details></validationReport></job></jobs></report>"""


class _Service(BaseHTTPRequestHandler):
    calls: list[str] = []

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Service.calls.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(REPORT)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id":"verapdf-rest","version":"1.30.2"}')

    def log_message(self, *a):
        pass


@pytest.fixture
def service(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Service.calls = []
    monkeypatch.setattr(get_settings(), "verapdf_url", f"http://127.0.0.1:{server.server_port}")
    yield _Service
    server.shutdown()


@pytest.fixture
def no_cli(monkeypatch):
    monkeypatch.setattr(verapdf, "_binary", lambda: None)


def test_the_service_is_used_when_configured(service, no_cli, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    res = run_verapdf(pdf, tmp_path / "report.xml", "ua1")
    assert res.via == "service" and service.calls == ["/api/validate/ua1"]
    assert res.compliant is False and res.failed_checks == 2
    assert res.failures[0].clause == "7.1" and res.failures[0].pages == [1, 4]
    assert (tmp_path / "report.xml").read_bytes() == REPORT
    assert verapdf.service_version(get_settings().verapdf_url) == "veraPDF REST 1.30.2"


def test_a_job_can_choose_the_command_line(service, no_cli, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    with job_validator({"validator": "command_line"}):
        res = run_verapdf(pdf, tmp_path / "report.xml", "ua1")
    assert service.calls == [] and res.available is False  # no command line here: reported, not hidden


def test_falls_back_to_the_command_line_when_the_service_is_down(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "verapdf_url", "http://127.0.0.1:9")  # nothing listens there
    called = []
    monkeypatch.setattr(verapdf, "_run_cli", lambda p, x, f: called.append(f) or verapdf.VeraResult(flavour=f, available=True, compliant=True, via="command line"))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    res = run_verapdf(pdf, tmp_path / "report.xml", "ua2")
    assert called == ["ua2"] and res.via == "command line" and res.compliant is True


def test_ua2_can_be_left_out(monkeypatch):
    monkeypatch.setattr(get_settings(), "verapdf_flavours", "ua1,ua2")
    assert flavours() == ["ua1", "ua2"]
    with job_validator({"verapdf_ua2": False}):
        assert flavours() == ["ua1"]
    assert flavours() == ["ua1", "ua2"]


def test_failed_rules_are_listed_in_clause_order():
    xml = REPORT.replace(b'clause="7.1" testNumber="3"', b'clause="7.21.5" testNumber="1"').replace(
        b"</rule></details>", b'</rule><rule specification="ISO 14289-1:2014" clause="7.2" testNumber="10" status="failed" '
        b'failedChecks="1"><description>x</description></rule><rule specification="ISO 14289-1:2014" clause="7.2" '
        b'testNumber="9" status="failed" failedChecks="1"><description>y</description></rule></details>')
    res = verapdf.parse_report(xml, "ua1")
    assert [(f.clause, f.test) for f in res.failures] == [("7.2", "9"), ("7.2", "10"), ("7.21.5", "1")]


def _inspect(stats, active=None):
    class Inspect:
        def stats(self):
            if isinstance(stats, Exception):
                raise stats
            return stats

        def active(self):
            return active

    return lambda timeout=1.0: Inspect()


def test_worker_capacity_adds_up_the_running_workers(monkeypatch, tmp_path):
    from app.api import routes
    from app.config import get_settings
    from app.worker.celery_app import celery_app

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    two = {"celery@a": {"pool": {"max-concurrency": 2}}, "celery@b": {"pool": {"max-concurrency": 2}}}
    monkeypatch.setattr(celery_app.control, "inspect", _inspect(two, {}))
    assert routes.worker_capacity() == {"workers": 2, "documents_at_once": 4, "documents_on_stopping_workers": 0}
    monkeypatch.setattr(celery_app.control, "inspect", _inspect(None))
    assert routes.worker_capacity() == {"workers": 0, "documents_at_once": None, "documents_on_stopping_workers": 0}
    monkeypatch.setattr(celery_app.control, "inspect", _inspect(ConnectionError("no queue")))
    assert routes.worker_capacity() == {"workers": None, "documents_at_once": None, "documents_on_stopping_workers": 0}


def test_a_stopping_workers_document_keeps_its_place(monkeypatch, tmp_path):
    """#531: during a deploy the old worker finishes its document but stops answering; the new one answers with a
    place free. The old worker's document still counts as a place, so the portal sends the next to the new worker.
    A document an answering worker has, a finished one and one whose heartbeat went stale don't add a place."""
    import json
    import time

    from app.api import routes
    from app.config import get_settings
    from app.storage import JobStore
    from app.worker.celery_app import celery_app

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    now = time.time()

    def job(job_id, beat_ago, finished=False):
        store = JobStore(job_id, tmp_path / "jobs")
        store.create()
        (store.dir / "heartbeat.json").write_text(json.dumps({"at": now - beat_ago}))
        if finished:
            store.write_result({"status": "completed"})

    old, new = "10000000-0000-4000-8000-000000000000", "20000000-0000-4000-8000-000000000000"
    job(old, 10)  # on the stopping worker
    job(new, 5)  # on the new worker
    job("30000000-0000-4000-8000-000000000000", 5, finished=True)
    job("40000000-0000-4000-8000-000000000000", 600)  # its worker was lost
    (tmp_path / "jobs" / "not-a-job").mkdir()
    answering = {"celery@new": {"pool": {"max-concurrency": 1}}}
    active = {"celery@new": [{"id": "t1", "name": "app.worker.tasks.remediate", "args": [new]}]}
    monkeypatch.setattr(celery_app.control, "inspect", _inspect(answering, active))
    assert routes.worker_capacity() == {"workers": 1, "documents_at_once": 2, "documents_on_stopping_workers": 1}
    # With no worker answering at all, each fresh document still holds its place
    monkeypatch.setattr(celery_app.control, "inspect", _inspect(None))
    assert routes.worker_capacity() == {"workers": 0, "documents_at_once": 2, "documents_on_stopping_workers": 2}


OOM_REPORT = (b"<report><jobs><job><taskResult><exceptionMessage>Exception: OutOfMemory caught when validating item "
              b"caused by exception: Java heap space</exceptionMessage></taskResult></job></jobs></report>")


def test_the_service_running_out_of_memory_isnt_retried_inside_the_worker(monkeypatch, tmp_path):
    """The command line runs in the worker beside the document, with less memory than the service: retried there on a
    300-page file, it took the worker down with it (production, 2026-10-16)."""
    monkeypatch.setattr(get_settings(), "verapdf_url", "http://verapdf.test")
    monkeypatch.setattr(verapdf, "_run_service", lambda url, p, f: OOM_REPORT)
    called = []
    monkeypatch.setattr(verapdf, "_run_cli", lambda p, x, f: called.append(f))
    res = verapdf.run_verapdf(tmp_path / "big.pdf", tmp_path / "big.xml", "ua1")
    assert called == [] and res.compliant is None
    assert "ran out of memory" in res.error and "VERAPDF_HEAP" in res.error
    # Any other unusable report still goes to the command line
    monkeypatch.setattr(verapdf, "_run_service", lambda url, p, f: b"<report/>")
    verapdf.run_verapdf(tmp_path / "big.pdf", tmp_path / "big.xml", "ua1")
    assert called == ["ua1"]


def test_the_command_line_gets_a_heap_of_its_own_size(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(verapdf, "_binary", lambda: "/usr/bin/verapdf")
    monkeypatch.setattr(get_settings(), "verapdf_cli_heap", "768m")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(verapdf.subprocess, "run", run)
    verapdf._run_cli(tmp_path / "a.pdf", tmp_path / "a.xml", "ua1")
    assert seen["JAVA_TOOL_OPTIONS"].startswith("-Xmx768m") and "PATH" in seen


def test_the_profiles_are_checked_side_by_side_through_the_service(monkeypatch, tmp_path):
    """Each check waits on the service, not on the worker: the two profiles at once save a minute and a half on a
    500-page document. The job's own validator choices (a context variable) reach both; the command line stays one
    at a time."""
    import threading
    import time

    from app.pipeline import orchestrator

    monkeypatch.setattr(get_settings(), "verapdf_flavours", "ua1,ua2")
    seen = []

    def check(pdf, xml, fl):
        seen.append((fl, threading.get_ident(), verapdf._options.get().service))
        time.sleep(0.4)
        return verapdf.VeraResult(flavour=fl, available=True, compliant=True)
    monkeypatch.setattr(orchestrator, "run_verapdf", check)
    monkeypatch.setattr(verapdf, "uses_service", lambda: True)
    started = time.monotonic()
    with verapdf.job_validator({"validator": "service"}):
        results = orchestrator._validate(tmp_path / "a.pdf", tmp_path, "after", "a")
    assert time.monotonic() - started < 0.7                         # side by side, not 0.8 s one after the other
    assert list(results) == ["ua1", "ua2"] and {s[2] for s in seen} == {True}
    assert len({s[1] for s in seen}) == 2

    seen.clear()
    monkeypatch.setattr(verapdf, "uses_service", lambda: False)    # the command line: one after the other
    orchestrator._validate(tmp_path / "a.pdf", tmp_path, "after", "a")
    assert len({s[1] for s in seen}) == 1
