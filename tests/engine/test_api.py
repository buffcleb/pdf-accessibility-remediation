import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    """Runs the Celery task synchronously in a worker thread (as a real worker would, outside the API loop)."""
    import threading

    from app.main import app
    from app.worker import tasks

    def run_inline(args, task_id=None, **kw):
        t = threading.Thread(target=lambda: _swallow(tasks.remediate.apply, args=args, task_id=task_id))
        t.start()
        t.join()

    def run_edit_inline(args, task_id=None, **kw):
        t = threading.Thread(target=lambda: _swallow(tasks.edit_tags.apply, args=args, task_id=task_id))
        t.start()
        t.join()

    monkeypatch.setattr(tasks.remediate, "apply_async", run_inline)
    monkeypatch.setattr(tasks.edit_tags, "apply_async", run_edit_inline)
    with TestClient(app) as c:
        yield c


def _swallow(fn, **kw):
    try:
        fn(**kw)
    except Exception:  # noqa: BLE001 - failures are recorded in error.json and asserted via the API
        pass


def test_submit_poll_download(client, samples):
    with (samples / "table_doc.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("table doc.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock", "flags": json.dumps({"return_base64": False})})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "processing"

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "completed", body
    assert body["metrics"]["pages_processed"] == 1
    assert body["metrics"]["ai_tokens_used"]["prompt_tokens"] > 0
    assert body["reports"]["comparison"]["before"]["tagged"] is False
    assert body["reports"]["comparison"]["after"]["tagged"] is True
    assert "existing_tags" in body["reports"]["comparison"]
    pdf = client.get(body["files"]["remediated_primary"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_base64_mode(client, samples):
    with (samples / "two_column.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("two_column.pdf", fh, "application/pdf")},
                        data={"ai_provider": "none", "flags": json.dumps({"return_base64": True})})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    import base64
    assert base64.b64decode(body["files"]["remediated_primary"]).startswith(b"%PDF")


def test_report_only(client, samples):
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("s.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock", "flags": json.dumps({"report_only": True})})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "completed"
    assert body["files"]["remediated_primary"] is None
    assert body["reports"]["comparison"]["after"] is None
    assert body["reports"]["comparison"]["ai_audit"]["figures"]


def test_rejects_non_pdf_and_bad_provider(client):
    r = client.post("/api/v1/remediate", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 415
    r = client.post("/api/v1/remediate", files={"file": ("x.pdf", b"%PDF-1.7\n", "application/pdf")},
                    data={"ai_provider": "skynet"})
    assert r.status_code == 422


def test_path_traversal_blocked(client):
    r = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000/files/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


def test_markdown_report(client, samples):
    with (samples / "partially_tagged.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("partial.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock", "flags": json.dumps({"return_base64": True})})
    job_id = r.json()["job_id"]
    md = client.get(f"/api/v1/jobs/{job_id}", params={"format": "markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    text = md.text
    assert text.startswith("# Accessibility remediation report: partial.pdf")
    for section in ("## Result", "## Files", "## Before and after", "## Existing tags", "## Metrics"):
        assert section in text
    assert "| Failed checks |" in text and "| Retained | Repaired | Replaced | Removed (empty) | Made artifact |" in text
    assert "base64" not in text.split("## Files")[1].split("##")[0]  # links even when JSON uses base64
    # the same report is saved next to the outputs and linked from the JSON
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    import base64
    saved = base64.b64decode(body["reports"]["markdown_report"]).decode()
    assert saved.startswith("# Accessibility remediation report")


def test_markdown_report_only_and_bad_format(client, samples):
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("s.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock", "flags": json.dumps({"report_only": True})})
    job_id = r.json()["job_id"]
    text = client.get(f"/api/v1/jobs/{job_id}?format=md").text
    assert "Report only" in text and "## Figures (AI audit)" in text
    assert client.get(f"/api/v1/jobs/{job_id}?format=xml").status_code == 422


def test_unreachable_ai_provider_fails_the_job(client, samples, monkeypatch):
    from app.ai.base import AIError
    from app.ai.mock import MockProvider

    async def down(self):
        raise AIError("cannot reach mock at http://nowhere: refused")

    monkeypatch.setattr(MockProvider, "preflight", down)
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("s.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock"})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "failed"
    assert body["error"]["error"] == "ai_provider_unavailable"
    assert "cannot reach" in body["error"]["detail"]


def test_tag_maps_and_edit_job(client, samples):
    """A remediation returns tag maps; an edit job applies edits to the output and validates it."""
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock"})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    maps = body["reports"]["tag_maps"]
    assert maps["original"] and maps["primary"]
    tagmap = client.get(maps["primary"]).json()
    original = client.get(maps["original"]).json()
    assert original["elements"] == [] and tagmap["elements"]
    findings = body["reports"]["tag_verification"]["primary"]["findings"]
    assert all(f["element"] < len(tagmap["elements"]) for f in findings if "element" in f)

    # Bookmarks from the headings, one per heading the tag map has
    heads = [e for e in tagmap["elements"] if e["role"] in ("H1", "H2", "H3", "H4", "H5", "H6") and e.get("text")]
    assert body["reports"]["comparison"]["bookmarks"] == {"bookmarks": len(heads), "outline": "none"}
    assert "Bookmarks: " in body["reports"]["remediation_summary"]

    pdf = client.get(body["files"]["remediated_primary"]).content
    p = next(e for e in tagmap["elements"] if e["role"] == "P" and e["own"])
    edits = [{"op": "set_role", "element": p["i"], "expect_role": "P", "role": "H2"}]
    r = client.post("/api/v1/edit", files={"file": ("report_remediated.pdf", pdf, "application/pdf")},
                    data={"edits": json.dumps(edits)})
    assert r.status_code == 202, r.text
    edited = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert edited["status"] == "completed", edited
    assert edited["reports"]["comparison"]["visual_fidelity"]["identical"] is True
    assert edited["reports"]["edits_applied"] == [f"element {p['i']}: P -> H2"]
    new_map = client.get(edited["reports"]["tag_maps"]["primary"]).json()
    assert new_map["elements"][p["i"]]["role"] == "H2"
    # The paragraph made a heading has its bookmark: the outline made at remediation follows the headings
    assert edited["reports"]["comparison"]["bookmarks"] == {"bookmarks": len(heads) + 1, "outline": "ours"}
    assert client.get(edited["files"]["remediated_primary"]).content.startswith(b"%PDF")

    # A stale edit is refused with a readable reason
    r = client.post("/api/v1/edit", files={"file": ("x.pdf", pdf, "application/pdf")},
                    data={"edits": json.dumps([{"op": "set_alt", "element": p["i"], "expect_role": "Figure", "alt": "x"}])})
    failed = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert failed["status"] == "failed" and failed["error"]["error"] == "edit_rejected"
    assert client.post("/api/v1/edit", files={"file": ("x.pdf", pdf, "application/pdf")},
                       data={"edits": "[]"}).status_code == 422


def test_corrected_file_is_checked_against_the_version_it_replaces(client, samples):
    """A corrected file uploaded by support: no edits, checked as it is, compared with the version it replaces."""
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock"})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    remediated = client.get(body["files"]["remediated_primary"]).content
    other = (samples / "table_doc.pdf").read_bytes()

    same = client.post("/api/v1/edit", data={"edits": "[]"},
                       files={"file": ("fixed.pdf", remediated, "application/pdf"),
                              "reference": ("before.pdf", remediated, "application/pdf")})
    assert same.status_code == 202, same.text
    done = client.get(f"/api/v1/jobs/{same.json()['job_id']}").json()
    assert done["status"] == "completed", done
    assert done["reports"]["remediation_summary"].startswith("Checked a corrected file.")
    assert done["reports"]["comparison"]["visual_fidelity"]["identical"] is True

    changed = client.post("/api/v1/edit", data={"edits": "[]"},
                          files={"file": ("fixed.pdf", remediated, "application/pdf"),
                                 "reference": ("before.pdf", other, "application/pdf")})
    done = client.get(f"/api/v1/jobs/{changed.json()['job_id']}").json()
    assert done["status"] == "completed", done
    assert done["reports"]["comparison"]["visual_fidelity"]["identical"] is False
    assert any("from the version it replaces" in w for w in done["warnings"])



def _pending_job(monkeypatch, samples, client):
    """A job that has been accepted but whose task never runs here (as if a worker had picked it up)."""
    from app.worker import tasks

    monkeypatch.setattr(tasks.remediate, "apply_async", lambda args, task_id=None, **kw: None)
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("doc.pdf", fh, "application/pdf")}, data={"ai_provider": "none"})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


class _Ended:
    def __init__(self, state, info):
        self.state, self.info = state, info


def test_a_job_whose_worker_was_killed_is_reported_as_failed(client, samples, monkeypatch):
    """The hard time limit kills the worker before it can write the failure down. The job must not stay
    "processing" for ever: the portal would wait for ever, holding a place and the person's pages."""
    from billiard.exceptions import TimeLimitExceeded, WorkerLostError

    from app.worker.celery_app import celery_app

    job_id = _pending_job(monkeypatch, samples, client)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "processing"
    monkeypatch.setattr(celery_app, "AsyncResult", lambda _id: _Ended("FAILURE", TimeLimitExceeded(3600)))
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "failed" and body["error"]["error"] == "time_limit_exceeded"
    # ...and it stays failed once the queue has forgotten the task
    monkeypatch.setattr(celery_app, "AsyncResult", lambda _id: _Ended("PENDING", None))
    assert client.get(f"/api/v1/jobs/{job_id}").json()["error"]["error"] == "time_limit_exceeded"

    other = _pending_job(monkeypatch, samples, client)
    monkeypatch.setattr(celery_app, "AsyncResult", lambda _id: _Ended("FAILURE", WorkerLostError("signal 9 (SIGKILL)")))
    assert client.get(f"/api/v1/jobs/{other}").json()["error"]["error"] == "worker_lost"


def test_a_job_can_be_stopped(client, samples, monkeypatch):
    from app.worker.celery_app import celery_app

    revoked = []
    monkeypatch.setattr(celery_app.control, "revoke", lambda task_id, **kw: revoked.append((task_id, kw)))
    job_id = _pending_job(monkeypatch, samples, client)
    r = client.post(f"/api/v1/jobs/{job_id}/stop")
    assert r.status_code == 200 and r.json()["stopped"] is True
    assert revoked == [(job_id, {"terminate": True, "signal": "SIGTERM"})]
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "failed" and body["error"]["error"] == "stopped"
    # Stopping again, or stopping a job that isn't there, changes nothing
    assert client.post(f"/api/v1/jobs/{job_id}/stop").json()["stopped"] is False and len(revoked) == 1
    assert client.post("/api/v1/jobs/00000000-0000-0000-0000-000000000000/stop").status_code == 404


def _figure_page(tmp_path):
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(100, 100, 200, 180), color=(0, 0, 0), fill=(0.8, 0.2, 0.2))
    page.insert_text((110, 200), "Step A", fontsize=10, fontname="helv")
    path = tmp_path / "figure.pdf"
    doc.save(path)
    return path


def test_an_area_of_a_page_is_described_by_the_ai_at_once(client, tmp_path):
    path = _figure_page(tmp_path)
    with path.open("rb") as f:
        r = client.post("/api/v1/describe", files={"file": ("figure.pdf", f, "application/pdf")},
                        data={"page": "1", "bbox": json.dumps([90, 90, 210, 210]), "ai_provider": "mock", "long": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alt"] and set(body) >= {"alt", "kind", "decorative", "model", "cost_usd", "prompt_tokens"}


def test_describing_an_area_refuses_bad_requests(client, tmp_path):
    path = _figure_page(tmp_path)
    for data, code in [({"page": "1", "bbox": "[1, 2, 3]"}, 422), ({"page": "1", "bbox": json.dumps([10, 10, 10, 50])}, 422),
                       ({"page": "3", "bbox": json.dumps([90, 90, 210, 210])}, 422),
                       ({"page": "1", "bbox": json.dumps([900, 900, 950, 950])}, 422),
                       ({"page": "1", "bbox": json.dumps([90, 90, 210, 210]), "ai_provider": "none"}, 422)]:
        with path.open("rb") as f:
            r = client.post("/api/v1/describe", files={"file": ("figure.pdf", f, "application/pdf")},
                            data={"ai_provider": "mock", **data})
        assert r.status_code == code, (data, r.text)
