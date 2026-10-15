from __future__ import annotations

import csv
import io
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pikepdf
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from portal import outbox, quota, worker
from portal.engine import EngineError, set_engine_client
from portal.mail import get_mailer
from portal.models import AuditEvent, Group, Job, QuotaLedger, User
from portal.quota import OverQuota
from portal.storage import get_storage
from tests.portal.conftest import login, make_group, make_user

API = "/api/v1"


def pdf_bytes(pages: int, password: str | None = None) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    buf = io.BytesIO()
    if password:
        pdf.save(buf, encryption=pikepdf.Encryption(user=password, owner=password))
    else:
        pdf.save(buf)
    return buf.getvalue()


class FakeEngine:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.submitted: list[tuple[str, bool]] = []
        self.ai_choices: list = []
        self.judges: list = []
        self.full_reviews: list = []
        self.routes: list = []
        self.models = ["local-vision", "commercial"]
        self.classified: list = []
        self.classify_answer: dict | Exception = {"kind": "general", "confidence": 1.0}
        self.described: list = []
        self.describe_answer: dict | Exception = {"alt": "Two rings joined by a bond", "kind": "chart", "decorative": False,
                                                  "model": "commercial", "cost_usd": 0.0012}

    def classify(self, filename, fileobj, kinds, backend, model=None, pages=3):
        self.classified.append((filename, [k["key"] for k in kinds], backend, model, pages))
        if isinstance(self.classify_answer, Exception):
            raise self.classify_answer
        return dict(self.classify_answer)

    def describe(self, filename, fileobj, page, bbox, model, long=False):
        self.described.append((filename, page, list(bbox), model, long, len(fileobj.read())))
        if isinstance(self.describe_answer, Exception):
            raise self.describe_answer
        return dict(self.describe_answer)

    def ai_models(self):
        return list(self.models)

    def submit(self, filename, fileobj, report_only, ai=None, full_review=False, route="standard", branding=None,
               no_ai=False, validator=None):
        jid = uuid.uuid4().hex
        self.validators = getattr(self, "validators", []) + [validator]
        self.submitted.append((filename, report_only))
        self.routes.append(route)
        self.ai_choices.append(ai)
        self.full_reviews.append(full_review)
        self.brandings = getattr(self, "brandings", []) + [branding]
        self.no_ais = getattr(self, "no_ais", []) + [no_ai]
        self.jobs[jid] = {"status": "processing", "progress": {"stage": "tagging"}}
        return jid

    def submit_edit(self, filename, fileobj, edits, review="none", judge=None, reference=None, validator=None):
        jid = uuid.uuid4().hex
        self.validators = getattr(self, "validators", []) + [validator]
        self.submitted.append((filename, "edit", edits, review))
        self.references = getattr(self, "references", []) + [reference.read() if reference else None]
        self.judges.append(judge)
        self.jobs[jid] = {"status": "processing"}
        return jid

    def complete_edit(self, jid, compliant=True, findings=None):
        base = f"/api/v1/jobs/{jid}/files"
        self.jobs[jid] = {
            "status": "completed",
            "files": {"remediated_primary": f"{base}/doc_reviewed.pdf", "remediated_visual_fallback": None},
            "reports": {
                "verapdf_after": {"primary": f"{base}/doc_verapdf_after_ua1.xml"},
                "comparison": {"after": {"compliant": compliant, "failed_checks": 0 if compliant else 3},
                               "visual_fidelity": {"identical": True}},
                "edits_applied": ["element 3: P -> H2"],
                "tag_verification": {"primary": {"integrity": {"passed": True}, "findings": findings or []}},
                "tag_maps": {"primary": f"{base}/doc_tagmap_primary.json"},
            },
        }

    def status(self, jid):
        if jid not in self.jobs:
            raise EngineError("gone")
        return self.jobs[jid]

    def fetch(self, path, out):
        if path.endswith(".pdf"):
            data = b"%PDF-1.7 remediated"
        elif "tagmap" in path:
            data = b'{"schema":1,"pages":[{"w":612,"h":792}],"elements":[]}'
        else:
            data = b"# Report\n\nAll good."
        out.write(data)
        return len(data)

    def complete(self, jid, two_files=False):
        base = f"/api/v1/jobs/{jid}/files"
        self.jobs[jid] = {
            "status": "completed",
            "files": {"remediated_primary": f"{base}/doc_compliant_modified.pdf" if two_files else f"{base}/doc_remediated.pdf",
                      "remediated_visual_fallback": f"{base}/doc_compliant_visual.pdf" if two_files else None},
            "metrics": {"pages_processed": 3, "processing_time_seconds": 4.2, "ai_calls": 2},
            "reports": {
                "verapdf_before": f"{base}/doc_verapdf_before_ua1.xml",
                "verapdf_after": {"primary": f"{base}/doc_verapdf_after_ua1.xml", "visual_fallback": None},
                "markdown_report": f"{base}/doc_remediation_report.md",
                "report_pdf": f"{base}/doc_remediation_report.pdf",
                "tag_maps": {"original": f"{base}/doc_tagmap_original.json", "primary": f"{base}/doc_tagmap_primary.json",
                             "visual_fallback": f"{base}/doc_tagmap_visual_fallback.json" if two_files else None},
                "full_report": f"{base}/doc_remediation_report.json",
                "comparison": {"before": {"compliant": False, "failed_checks": 120},
                               "after": {"compliant": True, "failed_checks": 0}},
                "remediation_summary": "Tagged.",
                "tag_verification": {"primary": {
                    "integrity": {"passed": True},
                    "summary": {"flagged": 2},
                    "findings": [{"severity": "suggest", "page": 2, "issue": "link text"},
                                 {"severity": "review", "page": 1, "issue": "alt text partly wrong"}]}},
            },
        }

    def fail(self, jid, code="tag_integrity_failed"):
        self.jobs[jid] = {"status": "failed", "error": {"error": code, "detail": "internal detail"}}

    def stop(self, jid):
        self.stopped = getattr(self, "stopped", []) + [jid]
        if getattr(self, "stop_unreachable", False):
            from portal.engine import EngineUnavailable
            raise EngineUnavailable("connection refused")
        self.jobs[jid] = {"status": "failed", "error": {"error": "stopped", "detail": "stopped"}}


@pytest.fixture
def fake_engine():
    fake = FakeEngine()
    set_engine_client(fake)
    yield fake
    set_engine_client(None)


@pytest.fixture
def setup(db, make_client, fake_engine):
    a = make_group(db, "Campus A", "a.test")
    b = make_group(db, "Campus B", "b.test")
    make_user(db, "root@admin.test", None, role="super_admin")
    make_user(db, "admin@a.test", a, admin_of=a)
    make_user(db, "admin@b.test", b, admin_of=b)
    c = {}
    for name, email in [("super", "root@admin.test"), ("admin_a", "admin@a.test"), ("admin_b", "admin@b.test"),
                        ("user", "user@a.test"), ("user2", "user2@a.test")]:
        c[name] = make_client()
        login(c[name], email)
    get_storage().objects.clear()
    get_mailer().outbox.clear()
    return {"a": a, "b": b, "c": c, "db": db, "engine": fake_engine}


def topup(s, group_id, pages, **kw):
    body = {"pages": pages, "ticket_ref": "RITM0012345", **kw}
    return s["c"]["super"].post(f"{API}/admin/groups/{group_id}/topups", json=body)


def upload(client, *docs, report_only=False):
    files = [("files", (name, data, "application/pdf")) for name, data in docs]
    return client.post(f"{API}/jobs", files=files, data={"report_only": str(report_only).lower()})


def run_worker(db: Session):
    worker.submit_queued(db)
    worker.poll_active(db)
    outbox.send_queued(db)


# --- top-ups -------------------------------------------------------------------------------------

def test_topup_needs_ticket_or_flagged_override(setup):
    s, a = setup, setup["a"].id
    sup = s["c"]["super"]
    r = sup.post(f"{API}/admin/groups/{a}/topups", json={"pages": 100})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "ticket_required"
    r = sup.post(f"{API}/admin/groups/{a}/topups", json={"pages": 100, "override": True})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "comment_required"
    r = topup(s, a, 1000)
    assert r.status_code == 201 and r.json()["pool_after"] == 1000 and r.json()["override"] is False
    r = sup.post(f"{API}/admin/groups/{a}/topups",
                 json={"pages": 50, "override": True, "comment": "Dean approved by phone; ticket to follow"})
    assert r.status_code == 201 and r.json()["override"] is True and r.json()["pool_after"] == 1050
    ledger = s["c"]["admin_a"].get(f"{API}/groups/{a}/ledger").json()
    assert [e["kind"] for e in ledger] == ["topup", "topup"] and ledger[0]["override"] is True
    ev = sup.get(f"{API}/admin/audit", params={"action": "quota.topup"}).json()
    assert {e["outcome"] for e in ev} == {"success", "override"}
    # Group admins can't top up
    assert s["c"]["admin_a"].post(f"{API}/admin/groups/{a}/topups", json={"pages": 1, "ticket_ref": "x"}).status_code == 403


def test_adjust_cannot_go_below_usage(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    upload(s["c"]["user"], ("d.pdf", pdf_bytes(6)))
    r = s["c"]["super"].post(f"{API}/admin/groups/{a}/adjustments", json={"pages": -5, "comment": "typo"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "below_usage"
    r = s["c"]["super"].post(f"{API}/admin/groups/{a}/adjustments", json={"pages": -4, "comment": "typo"})
    assert r.status_code == 201 and r.json()["pool_after"] == 6


def test_ledger_is_append_only(setup):
    topup(setup, setup["a"].id, 10)
    db = setup["db"]
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        db.execute(text("update quota_ledger set pages = 1000"))
    db.rollback()


# --- uploads and quota ---------------------------------------------------------------------------

def test_upload_reserves_and_completion_commits(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 100)
    r = upload(s["c"]["user"], ("Report Q3.pdf", pdf_bytes(3)))
    assert r.status_code == 200, r.text
    res = r.json()[0]
    assert res["accepted"] and res["job"]["status"] == "queued" and res["job"]["pages_charged"] == 3
    me = s["c"]["user"].get(f"{API}/me").json()
    assert me["reserved_pages"] == 3 and me["remaining_pages"] == 97

    db = s["db"]
    run_worker(db)
    job_id = res["job"]["id"]
    eng_id = db.get(Job, uuid.UUID(job_id)).engine_job_id
    assert s["engine"].submitted == [("Report_Q3.pdf", False)]
    s["engine"].complete(eng_id)
    run_worker(db)

    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["result"]["compliant_after"] is True and job["result"]["failed_checks_before"] == 120
    assert job["result"]["review"]["findings"][0]["severity"] == "review"  # sorted by severity
    kinds = {f["kind"] for f in job["files"]}
    assert {"input", "primary", "report_md", "report_json", "verapdf_before", "verapdf_after", "report_pdf"} <= kinds
    assert next(f for f in job["files"] if f["kind"] == "report_pdf")["label"] == "Report (PDF)"
    # The engine is told how to brand the PDF report (and never given a model name for it)
    assert s["engine"].brandings[-1]["service_name"] and "ai_label" not in s["engine"].brandings[-1]
    # ...and how to run veraPDF: by default its running service, with PDF/UA-2 shown for information
    assert s["engine"].validators[-1] == {"validator": "service", "verapdf_ua2": True}
    me = s["c"]["user"].get(f"{API}/me").json()
    assert me["used_pages"] == 3 and me["reserved_pages"] == 0
    grp = s["c"]["admin_a"].get(f"{API}/groups/{a}").json()["balance"]
    assert grp == {"pool_pages": 100, "used_pages": 3, "reserved_pages": 0, "remaining_pages": 97}
    mail = get_mailer().outbox[-1]
    assert mail.to == "user@a.test" and mail.subject == "Done: Report Q3.pdf" and job_id in mail.text

    pdf = next(f for f in job["files"] if f["kind"] == "primary")
    r = s["c"]["user"].get(f"{API}/jobs/{job_id}/files/{pdf['id']}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/report").json()["markdown"].startswith("# Report")


def test_failure_releases_pages_and_hides_internals(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 100)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(5))).json()[0]["job"]["id"]
    db = s["db"]
    run_worker(db)
    s["engine"].fail(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["pages_charged"] == 0
    assert "internal detail" not in job["error_message"] and job["error_code"] == "tag_integrity_failed"
    me = s["c"]["user"].get(f"{API}/me").json()
    assert me["reserved_pages"] == 0 and me["used_pages"] == 0
    kinds = [e["kind"] for e in s["c"]["admin_a"].get(f"{API}/groups/{a}/ledger").json()]
    assert kinds == ["release", "reserve", "topup"]
    assert get_mailer().outbox[-1].subject == "Failed: d.pdf"


def test_lost_engine_job_fails_and_releases(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 100)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    db = s["db"]
    run_worker(db)
    s["engine"].jobs.clear()
    run_worker(db)
    assert db.get(Job, uuid.UUID(job_id)).status == "failed"
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 0


def test_over_group_pool_shows_configured_message(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 5)
    s["c"]["super"].patch(f"{API}/admin/settings", json={
        "over_quota_message": "Out of pages. Open a ticket.", "support_url": "https://help.example.edu/pdf"})
    r = upload(s["c"]["user"], ("big.pdf", pdf_bytes(6)))
    err = r.json()[0]["error"]
    assert not r.json()[0]["accepted"]
    assert err["error"] == "over_quota" and err["scope"] == "group" and err["remaining_pages"] == 5
    # Which level ran out, then the administrators' configured message
    assert err["message"].startswith("Your group has 5 Standard pages left and this document needs 6.")
    assert err["message"].endswith("Out of pages. Open a ticket.") and err["support_url"] == "https://help.example.edu/pdf"
    # A group-specific message wins
    s["c"]["super"].patch(f"{API}/admin/groups/{a}", json={"over_quota_message": "Campus A: email the library."})
    err = upload(s["c"]["user"], ("big.pdf", pdf_bytes(6))).json()[0]["error"]
    assert err["message"].endswith("Campus A: email the library.")  # the group's own message wins


def test_user_cap_binds_and_oversubscription_is_allowed(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    ids = {u["email"]: u["id"] for u in s["c"]["admin_a"].get(f"{API}/groups/{a}/users").json()}
    # Caps add up to more than the pool (8 + 8 > 10)
    for email in ("user@a.test", "user2@a.test"):
        assert s["c"]["admin_a"].patch(f"{API}/groups/{a}/users/{ids[email]}", json={"cap_pages": 8}).status_code == 200
    r = upload(s["c"]["user"], ("d.pdf", pdf_bytes(9))).json()[0]
    assert r["error"]["scope"] == "user" and r["error"]["remaining_pages"] == 8
    assert upload(s["c"]["user"], ("d.pdf", pdf_bytes(7))).json()[0]["accepted"]
    # user2 is under their cap but the pool only has 3 left
    r = upload(s["c"]["user2"], ("d.pdf", pdf_bytes(4))).json()[0]
    assert r["error"]["scope"] == "group" and r["error"]["remaining_pages"] == 3


def test_report_only_uses_no_quota(setup):
    s = setup
    r = upload(s["c"]["user"], ("d.pdf", pdf_bytes(4)), report_only=True).json()[0]
    assert r["accepted"] and r["job"]["pages_charged"] == 0
    run_worker(s["db"])
    assert s["engine"].submitted[0][1] is True


def test_bad_files_are_refused_per_file(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 100)
    r = upload(s["c"]["user"], ("notes.pdf", b"hello, not a pdf"), ("locked.pdf", pdf_bytes(1, password="pw")),
               ("good.pdf", pdf_bytes(1)))
    by_name = {x["filename"]: x for x in r.json()}
    assert by_name["notes.pdf"]["error"]["error"] == "not_pdf"
    assert by_name["locked.pdf"]["error"]["error"] == "encrypted_pdf"
    assert by_name["good.pdf"]["accepted"]
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 1


def test_limits_from_settings(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 1000)
    s["c"]["super"].patch(f"{API}/admin/settings", json={"max_pages_per_document": 5,
                                                         "max_queued_jobs_per_user": 2})
    assert upload(s["c"]["user"], ("d.pdf", pdf_bytes(6))).json()[0]["error"]["error"] == "too_many_pages"
    assert upload(s["c"]["user"], ("1.pdf", pdf_bytes(1)), ("2.pdf", pdf_bytes(1))).status_code == 200
    r = upload(s["c"]["user"], ("3.pdf", pdf_bytes(1))).json()[0]
    assert r["error"]["error"] == "too_many_jobs"


def test_cancel_queued_job_releases(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(4))).json()[0]["job"]["id"]
    assert s["c"]["user2"].post(f"{API}/jobs/{job_id}/cancel").status_code == 404
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    assert s["c"]["user"].get(f"{API}/me").json()["reserved_pages"] == 0


def test_parallel_reservations_cannot_overspend(setup):
    """Ten threads race for a pool with room for three jobs."""
    s, a = setup, setup["a"].id
    topup(s, a, 30)
    db = s["db"]
    user_id = db.scalar(select(User.id).where(User.email == "user@a.test"))
    ok, refused = [], []
    barrier = threading.Barrier(10)

    def attempt():
        with Session(db.get_bind(), expire_on_commit=False) as sess:
            user = sess.get(User, user_id)
            barrier.wait()
            try:
                quota.reserve(sess, user, 10, uuid.uuid4())
                sess.commit()
                ok.append(1)
            except OverQuota:
                sess.rollback()
                refused.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 3 and len(refused) == 7
    db.expire_all()
    g = db.get(Group, a)
    assert g.service("standard").reserved_pages == 30


# --- access to files -----------------------------------------------------------------------------

def test_admin_file_access_is_scoped_and_recorded(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    job = upload(s["c"]["user"], ("private.pdf", pdf_bytes(1))).json()[0]["job"]
    f = job["files"][0]
    # Owner: not recorded
    assert s["c"]["user"].get(f"{API}/jobs/{job['id']}/files/{f['id']}").status_code == 200
    # Another user in the same group and another group's admin can't see it
    assert s["c"]["user2"].get(f"{API}/jobs/{job['id']}").status_code == 404
    assert s["c"]["admin_b"].get(f"{API}/jobs/{job['id']}/files/{f['id']}").status_code == 404
    # The group admin can, with a reason, and it's recorded
    r = s["c"]["admin_a"].get(f"{API}/jobs/{job['id']}/files/{f['id']}", params={"reason": "user asked for help"})
    assert r.status_code == 200
    ev = s["c"]["admin_a"].get(f"{API}/groups/{a}/audit", params={"action": "file.accessed"}).json()
    assert len(ev) == 1 and ev[0]["actor_email"] == "admin@a.test"
    assert ev[0]["details"]["owner"] == "user@a.test" and ev[0]["details"]["reason"] == "user asked for help"
    jobs = s["c"]["admin_a"].get(f"{API}/groups/{a}/jobs").json()
    assert jobs[0]["user_email"] == "user@a.test"


# --- retention and purge -------------------------------------------------------------------------

def test_retention_deletes_files_but_keeps_usage(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    db = s["db"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    assert get_storage().objects
    old = datetime.now(timezone.utc) - timedelta(days=31)
    db.execute(update(Job).values(created_at=old))
    db.commit()
    counts = worker.sweep(db)
    assert counts["files_jobs"] == 1 and not get_storage().objects
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    assert job["files_deleted"] and job["files"] == []
    assert s["c"]["user"].get(f"{API}/me").json()["used_pages"] == 2  # usage survives


def test_records_purge(setup):
    s = setup
    db = s["db"]
    s["c"]["super"].patch(f"{API}/admin/settings", json={"record_retention_days": 30})
    # Backdate the rows (the append-only trigger has to be lifted to do it, which only the table owner can)
    db.execute(text("alter table audit_events disable trigger audit_events_no_update"))
    db.execute(text("update audit_events set created_at = now() - interval '40 days'"))
    db.execute(text("alter table audit_events enable trigger audit_events_no_update"))
    db.commit()
    counts = worker.sweep(db)
    assert counts["audit_events"] > 0
    remaining = db.scalars(select(AuditEvent.action)).all()
    assert remaining == ["records.purged"]


# --- CSV -----------------------------------------------------------------------------------------

def test_csv_exports_are_scoped_safe_and_audited(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    db = s["db"]
    db.execute(update(User).where(User.email == "user@a.test").values(name="=HYPERLINK(\"http://x\")"))
    db.commit()
    r = s["c"]["admin_a"].get(f"{API}/groups/{a}/reports/usage.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    emails = {row[1] for row in rows[1:]}
    assert emails == {"admin@a.test", "user@a.test", "user2@a.test"}
    assert any(row[2].startswith("'=") for row in rows[1:])  # formula neutralised
    assert s["c"]["admin_b"].get(f"{API}/groups/{a}/reports/usage.csv").status_code == 404
    r = s["c"]["super"].get(f"{API}/admin/reports/groups.csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    row = dict(zip(rows[0], rows[1]))
    assert row["group"] == "Campus A" and row["service"] == "standard" and row["pool_pages"] == "10"
    assert s["c"]["super"].get(f"{API}/admin/reports/access.csv").status_code == 200
    assert s["c"]["super"].get(f"{API}/admin/reports/ledger.csv").status_code == 200
    ev = s["c"]["super"].get(f"{API}/admin/audit", params={"action": "report.exported"}).json()
    assert len(ev) == 4
    assert s["c"]["admin_a"].get(f"{API}/admin/reports/groups.csv").status_code == 403


def test_jobs_listing_is_per_user(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 10)
    upload(s["c"]["user"], ("mine.pdf", pdf_bytes(1)))
    assert [j["filename"] for j in s["c"]["user"].get(f"{API}/jobs").json()] == ["mine.pdf"]
    assert s["c"]["user2"].get(f"{API}/jobs").json() == []
    assert db_count(s["db"], QuotaLedger) == 2


def db_count(db, model) -> int:
    return len(db.scalars(select(model)).all())


def test_big_batch_is_queued_and_shared_fairly(setup):
    """A large upload is accepted in full; each user runs at most N at once, so others aren't starved."""
    s, a = setup, setup["a"].id
    topup(s, a, 1000)
    s["c"]["super"].patch(f"{API}/admin/settings", json={"max_concurrent_jobs_per_user": 2})
    r = upload(s["c"]["user"], *[(f"{i}.pdf", pdf_bytes(1)) for i in range(8)])
    assert all(x["accepted"] for x in r.json())
    upload(s["c"]["user2"], ("other.pdf", pdf_bytes(1)))  # uploaded after the batch
    db = s["db"]
    worker.submit_queued(db)  # 4 engine slots
    rows = db.execute(select(User.email, Job.status).join(Job, Job.user_id == User.id)).all()
    running = [e for e, st in rows if st == "submitted"]
    assert running.count("user@a.test") == 2 and running.count("user2@a.test") == 1
    assert [st for e, st in rows if e == "user@a.test"].count("queued") == 6


def test_super_admins_choose_how_verapdf_runs(setup):
    s = setup
    topup(s, s["a"].id, 100)
    r = s["c"]["super"].patch(f"{API}/admin/settings", json={"verapdf_mode": "command_line", "verapdf_ua2": False})
    assert r.status_code == 200, r.text
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"verapdf_mode": "fast"}).status_code == 422
    assert upload(s["c"]["user"], ("a.pdf", pdf_bytes(1))).status_code == 200
    run_worker(s["db"])
    assert s["engine"].validators[-1] == {"validator": "command_line", "verapdf_ua2": False}


def test_jobs_at_the_engine_follow_its_running_workers(setup, monkeypatch):
    """With workers scaled by the queue, the portal hands over as many documents as there are places."""
    s = setup
    topup(s, s["a"].id, 100)
    ps = worker.get_portal_settings()
    monkeypatch.setattr(ps, "engine_max_in_flight", 2)
    monkeypatch.setattr(ps, "engine_capacity_cache_seconds", 0.0)
    monkeypatch.setattr(worker, "_capacity", (0.0, None))  # restored afterwards, so later tests don't see this engine's
    s["c"]["super"].patch(f"{API}/admin/settings", json={"max_concurrent_jobs_per_user": 50})
    docs = [(f"d{n}.pdf", pdf_bytes(1)) for n in range(8)]
    assert upload(s["c"]["user"], *docs).status_code == 200
    at_engine = lambda: s["db"].scalar(select(func.count()).select_from(Job).where(Job.status == "submitted"))  # noqa: E731

    worker.submit_queued(s["db"])  # the engine can't say: the configured number
    assert at_engine() == 2
    s["engine"].capacity = lambda: 6  # three pods of two
    worker.submit_queued(s["db"])
    assert at_engine() == 6
    monkeypatch.setattr(ps, "engine_in_flight_ceiling", 7)
    s["engine"].capacity = lambda: 40
    worker.submit_queued(s["db"])
    assert at_engine() == 7
