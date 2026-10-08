"""The Adobe Autotag service: seeded off by the migration, sent to the engine's adobe route, no AI tier,
confirmation before sending, Adobe's file kept as its own version, pages given back on failure."""
from __future__ import annotations

from sqlalchemy import select, text

from portal.models import Job, QuotaLedger
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_jobs import (  # noqa: F401  (fixtures)
    API, fake_engine, pdf_bytes, run_worker, setup, topup,
)

ADOBE_SERVICE = """
    insert into services (key, kind, label, description, enabled, external, notice_enabled, notice_text, metered,
                          used_pages, reserved_pages, confirm_before_send, engine_route, sort)
    values ('adobe', 'processing', 'Adobe Autotag', 'Tagged by Adobe.', false, true, true,
            'Documents are sent to Adobe PDF Services.', true, 0, 0, true, 'adobe', 20)
"""


def _adobe(s, pages=50):
    """The service as migration 0011 seeds it, then switched on for group A."""
    s["db"].execute(text(ADOBE_SERVICE))
    s["db"].commit()
    sup, a = s["c"]["super"], s["a"].id
    assert sup.patch(f"{API}/admin/services/adobe", json={"enabled": True}).status_code == 200
    assert s["c"]["admin_a"].patch(f"{API}/groups/{a}/services/adobe",
                                   json={"enabled": True, "all_users": True}).status_code == 200
    topup(s, a, 50)
    if pages:
        topup(s, a, pages, service="adobe")


def _send(client, *docs, confirm=True, **data):
    files = [("files", (n, d, "application/pdf")) for n, d in docs]
    return client.post(f"{API}/jobs", files=files,
                       data={"processing": "adobe", "confirm": str(confirm).lower(), **data})


def _complete_adobe(engine, jid, reports=1):
    base = f"/api/v1/jobs/{jid}/files"
    engine.jobs[jid] = {
        "status": "completed",
        "files": {"remediated_primary": f"{base}/doc_adobe_tagged.pdf", "remediated_visual_fallback": None},
        "metrics": {"pages_processed": 2, "processing_time_seconds": 30.0, "ai_calls": 0, "route": "adobe",
                    "adobe": {"parts": reports, "pages_sent": 2, "requests": 9, "retries": 1, "seconds": 25.5}},
        "reports": {
            "verapdf_before": f"{base}/doc_verapdf_before_ua1.xml",
            "verapdf_after": {"primary": f"{base}/doc_verapdf_after_ua1.xml", "visual_fallback": None},
            "markdown_report": f"{base}/doc_remediation_report.md",
            "full_report": f"{base}/doc_remediation_report.json",
            "tag_maps": {"original": f"{base}/doc_tagmap_original.json", "primary": f"{base}/doc_tagmap_primary.json"},
            "adobe_reports": [f"{base}/doc_adobe_report_part{i + 1}.xlsx" for i in range(reports)],
            "comparison": {"before": {"compliant": False}, "after": {"compliant": False, "failed_checks": 4}},
            "tag_verification": {"primary": {"integrity": {"passed": True}, "findings": []}},
        },
    }


def test_adobe_without_ai_alt_text(setup):
    s = setup
    _adobe(s)
    _tier(s, "Cloud AI", "cloud-vision", metered=False, default=True)
    user = s["c"]["user"]
    choice = next(c for c in user.get(f"{API}/services").json() if c["key"] == "adobe")
    assert choice["external"] and choice["confirm_before_send"] and choice["ai_use"] == "alt_text" and choice["notice"]
    assert next(c for c in user.get(f"{API}/services").json() if c["key"] == "standard")["ai_use"] == "full"

    r = _send(user, ("doc.pdf", pdf_bytes(2)), ai_tier="ai:cloud-ai", full_review="true", ai_alt_text="false").json()[0]
    assert r["accepted"], r
    job = r["job"]
    assert job["ai_tier"] is None and not job["full_review"]  # alt text not wanted: no AI tier
    assert job["charged_services"] == ["adobe"] and job["external_services"] == ["adobe"]
    run_worker(s["db"])
    eng = s["engine"]
    assert eng.routes == ["adobe"] and eng.ai_choices == [None]

    jid = next(iter(eng.jobs))
    _complete_adobe(eng, jid, reports=2)
    run_worker(s["db"])
    body = user.get(f"{API}/jobs/{job['id']}").json()
    assert body["status"] == "completed"
    assert [f["kind"] for f in body["files"]].count("adobe_report") == 2
    versions = user.get(f"{API}/jobs/{job['id']}/versions").json()
    assert [v["label"] for v in versions["versions"]] == ["Original", "Adobe tagged"]
    s["db"].expire_all()
    j = s["db"].get(Job, job["id"])
    assert j.cost["adobe"]["pages_sent"] == 2 and j.result_summary["adobe"]["parts"] == 2
    used = s["db"].scalars(select(QuotaLedger).where(QuotaLedger.job_id == j.id, QuotaLedger.kind == "commit")).all()
    assert {e.service_key for e in used} == {"adobe"}


def test_adobe_asks_for_confirmation_and_reports_stay_local(setup):
    s = setup
    _adobe(s)
    user = s["c"]["user"]
    held = _send(user, ("doc.pdf", pdf_bytes(2)), confirm=False).json()[0]["job"]
    assert held["status"] == "held" and held["needs_confirmation"]
    run_worker(s["db"])
    assert s["engine"].routes == []  # nothing goes to Adobe until confirmed
    assert user.post(f"{API}/jobs/{held['id']}/confirm").status_code == 200
    run_worker(s["db"])
    assert s["engine"].routes == ["adobe"]

    rep = _send(user, ("doc.pdf", pdf_bytes(2)), confirm=False, report_only="true").json()[0]["job"]
    assert rep["processing"] == "standard" and rep["status"] == "queued" and rep["external_services"] == []
    run_worker(s["db"])
    assert s["engine"].routes[-1] == "standard"


def test_adobe_failure_gives_the_pages_back(setup):
    s = setup
    _adobe(s, pages=10)
    user = s["c"]["user"]
    job = _send(user, ("doc.pdf", pdf_bytes(3))).json()[0]["job"]
    run_worker(s["db"])
    jid = next(iter(s["engine"].jobs))
    s["engine"].fail(jid, "adobe_quota")
    run_worker(s["db"])
    body = user.get(f"{API}/jobs/{job['id']}").json()
    assert body["status"] == "failed" and "Adobe's usage limit" in body["error_message"]
    adobe = next(c for c in user.get(f"{API}/services").json() if c["key"] == "adobe")
    assert adobe["remaining_pages"] == 10


def test_adobe_stays_external(setup):
    s = setup
    _adobe(s, pages=0)
    r = s["c"]["super"].patch(f"{API}/admin/services/adobe", json={"external": False})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "external_required"
    svc = next(x for x in s["c"]["super"].get(f"{API}/admin/services").json() if x["key"] == "adobe")
    assert svc["engine_route"] == "adobe"


def _complete_with_alt_text(engine, jid, ai_calls=3):
    _complete_adobe(engine, jid, reports=1)
    base = f"/api/v1/jobs/{jid}/files"
    resp = engine.jobs[jid]
    resp["files"]["enhanced"] = f"{base}/doc_adobe_improved.pdf" if ai_calls else None
    resp["reports"]["improvements"] = {"alt_text": ai_calls, "decorative": 0, "unicode_maps": [], "glyph_lists": [],
                                       "glyph_widths": 0} if ai_calls else None
    resp["metrics"].update(ai_calls=ai_calls, images_described=ai_calls, ai_cost_usd=0.002 * ai_calls)
    if ai_calls:
        resp["reports"]["verapdf_after"]["enhanced"] = f"{base}/doc_verapdf_after_ai_alt_text_ua1.xml"
        resp["reports"]["tag_maps"]["enhanced"] = f"{base}/doc_tagmap_enhanced.json"
        resp["reports"]["comparison"]["after_enhanced"] = {"compliant": True, "failed_checks": 0}
        resp["reports"]["tag_verification"]["enhanced"] = {"integrity": {"passed": True}, "findings": []}


def test_adobe_with_ai_alt_text_adds_a_separate_version(setup):
    s = setup
    _adobe(s)
    cloud = _tier(s, "Cloud AI", "cloud-vision", metered=False, default=True, judge="cloud-judge")
    user = s["c"]["user"]
    job = _send(user, ("doc.pdf", pdf_bytes(2)), full_review="true").json()[0]["job"]  # alt text is on by default
    assert job["ai_tier"] == cloud and job["full_review"] and job["charged_services"] == ["adobe", cloud]
    run_worker(s["db"])
    eng = s["engine"]
    assert eng.routes == ["adobe"] and eng.ai_choices[0].model == "cloud-vision" and eng.full_reviews == [True]
    _complete_with_alt_text(eng, next(iter(eng.jobs)))
    run_worker(s["db"])
    versions = user.get(f"{API}/jobs/{job['id']}/versions").json()["versions"]
    assert [(v["kind"], v["label"]) for v in versions] == [
        ("original", "Original"), ("primary", "Adobe tagged"), ("enhanced", "Adobe + AI alt text")]
    assert versions[2]["summary"]["compliant"] is True
    body = user.get(f"{API}/jobs/{job['id']}").json()
    labels = {f["kind"]: f["label"] for f in body["files"]}
    assert labels["primary"] == "Adobe tagged PDF" and labels["enhanced"] == "Adobe + AI alt text (PDF)"
    assert not body["ai_refunded"]


def test_adobe_ai_pages_given_back_when_no_figure_needed_alt_text(setup):
    s = setup
    _adobe(s)
    big = _tier(s, "Large AI", "large-vision", pages=10, default=True)
    user = s["c"]["user"]
    job = _send(user, ("doc.pdf", pdf_bytes(2))).json()[0]["job"]
    assert job["charged_services"] == ["adobe", big]
    run_worker(s["db"])
    _complete_with_alt_text(s["engine"], next(iter(s["engine"].jobs)), ai_calls=0)
    run_worker(s["db"])
    body = user.get(f"{API}/jobs/{job['id']}").json()
    assert body["status"] == "completed" and body["ai_refunded"]
    tier = next(c for c in user.get(f"{API}/services").json() if c["key"] == big)
    assert tier["remaining_pages"] == 10
    versions = user.get(f"{API}/jobs/{job['id']}/versions").json()["versions"]
    assert [v["kind"] for v in versions] == ["original", "primary"]


def test_adobe_improved_copy_is_named_by_what_was_done(setup):
    from portal.worker import improved_label

    def resp(**imp):
        return {"reports": {"improvements": {"alt_text": 0, "glyph_lists": [], **imp}}}

    assert improved_label(resp(alt_text=3)) == "Adobe + AI alt text"
    assert improved_label(resp(glyph_lists=["Arial: incomplete CIDSet removed"])) == "Adobe + fixes"
    assert improved_label(resp(alt_text=1, glyph_widths=4)) == "Adobe + AI alt text and fixes"
