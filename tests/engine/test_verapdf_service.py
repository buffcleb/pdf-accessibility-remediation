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


def test_worker_capacity_adds_up_the_running_workers(monkeypatch):
    from app.api import routes
    from app.worker.celery_app import celery_app

    class Inspect:
        def __init__(self, stats):
            self._stats = stats

        def stats(self):
            if isinstance(self._stats, Exception):
                raise self._stats
            return self._stats

    two = {"celery@a": {"pool": {"max-concurrency": 2}}, "celery@b": {"pool": {"max-concurrency": 2}}}
    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout=1.0: Inspect(two))
    assert routes.worker_capacity() == {"workers": 2, "documents_at_once": 4}
    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout=1.0: Inspect(None))
    assert routes.worker_capacity() == {"workers": 0, "documents_at_once": None}
    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout=1.0: Inspect(ConnectionError("no queue")))
    assert routes.worker_capacity() == {"workers": None, "documents_at_once": None}
