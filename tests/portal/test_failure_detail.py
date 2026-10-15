"""What the engine says about a failure (paths, internal messages) reaches the super-admin note of the help
request and the log, not the audit rows that group admins read."""
import uuid

from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from portal.models import Job


def test_the_audit_row_carries_the_code_and_not_the_engine_detail(setup):
    s, a = setup, setup["a"].id
    topup(s, a, 100)
    job_id = upload(s["c"]["user"], ("d.pdf", pdf_bytes(5))).json()[0]["job"]["id"]
    db = s["db"]
    run_worker(db)
    s["engine"].fail(db.get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(db)
    rows = s["c"]["admin_a"].get(f"{API}/groups/{a}/audit", params={"action": "job.failed"}).json()
    assert len(rows) == 1 and rows[0]["details"] == {"code": "tag_integrity_failed"}
    assert "internal detail" not in str(rows)
