"""#409: a PDF 2.0 document is judged by PDF/UA-2; the portal keeps which part each verdict is by and says so."""
from __future__ import annotations

from types import SimpleNamespace

from portal import worker


def _resp(part: int) -> dict:
    flavour = f"ua{part}"
    snap = {"pdfua_part": part, "compliant": False, "failed_checks": 3, "failed_rules": 1,
            "verapdf": {flavour: {"failures": [{"clause": "8.2.5.2", "description": "Document in the PDF 2.0 namespace",
                                                "failed_checks": 1, "pages": []}]},
                        "ua1" if part == 2 else "ua2": {"failures": [{"clause": "6.1", "description": "header"}]}}}
    return {"files": {}, "metrics": {}, "reports": {"comparison": {"before": dict(snap), "after": snap}}}


def test_the_portal_keeps_which_part_a_verdict_is_by_and_its_failures():
    s = worker.summarize(_resp(2))
    assert s["pdfua_part"] == 2 and [f["clause"] for f in s["remaining_failures"]] == ["8.2.5.2"]
    assert worker.summarize(_resp(1))["pdfua_part"] == 1
    assert worker.version_summary(_resp(2), "primary")["pdfua_part"] == 2
    assert worker.version_summary({"reports": {}}, "primary")["pdfua_part"] == 1  # older results: PDF/UA-1


def test_the_result_email_names_the_part():
    job = SimpleNamespace(id="j1", status="completed", filename="paper.pdf", error_message=None,
                          user=SimpleNamespace(email="a@b.example", display_name="A"),
                          result_summary={"compliant_after": True, "pdfua_part": 2, "review": {"findings_total": 0}})
    assert "passes the PDF/UA-2 accessibility check" in worker._job_mail(job).text
    job.result_summary = {"compliant_after": True}
    assert "passes the PDF/UA-1 accessibility check" in worker._job_mail(job).text
