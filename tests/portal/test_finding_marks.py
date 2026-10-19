"""#366: review items signed off per document (checked, fixed, not an issue), by a key made from what the item is
about, so the status stays with the same item in a later version; and the list as CSV with the statuses."""
from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import select

from portal import finding_marks
from portal.models import AuditEvent, Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def _done(s) -> str:
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("Budget =SUM.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    return job_id


def test_the_key_follows_the_item_not_its_place():
    a = {"severity": "review", "issue": "alt text partly wrong", "page": 1, "role": "Figure", "text": "A chart",
         "element": 7}
    assert finding_marks.key(a) == finding_marks.key({**a, "element": 40, "severity": "high"})  # renumbered tags
    assert finding_marks.key(a) != finding_marks.key({**a, "page": 2})
    assert len(finding_marks.key(a)) == 20


def test_items_are_signed_off_by_who_can_edit_and_seen_by_who_can_see(setup):
    s = setup
    job_id = _done(s)
    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    items = job["result"]["review"]["findings"]
    assert all(len(f["key"]) == 20 for f in items)
    k = items[0]["key"]
    r = s["c"]["user"].put(f"{API}/jobs/{job_id}/finding-marks/{k}", json={"status": "not_issue", "note": " Fine. "})
    assert r.status_code == 200 and r.json()["status"] == "not_issue" and r.json()["note"] == "Fine."
    # The group admin may change it; a super admin sees it but can't; someone outside the group sees nothing
    assert s["c"]["admin_a"].put(f"{API}/jobs/{job_id}/finding-marks/{k}", json={"status": "fixed"}).status_code == 200
    seen = s["c"]["super"].get(f"{API}/jobs/{job_id}/finding-marks").json()
    assert not seen["can_mark"] and [(m["key"], m["status"]) for m in seen["marks"]] == [(k, "fixed")]
    assert s["c"]["super"].put(f"{API}/jobs/{job_id}/finding-marks/{k}", json={"status": "checked"}).status_code == 403
    assert s["c"]["user2"].get(f"{API}/jobs/{job_id}/finding-marks").status_code == 404
    bad = s["c"]["user"].put(f"{API}/jobs/{job_id}/finding-marks/not-a-key", json={"status": "checked"})
    assert bad.status_code == 422
    assert s["c"]["user"].put(f"{API}/jobs/{job_id}/finding-marks/{k}", json={"status": "done"}).status_code == 422
    # Taken back: not looked at again
    assert s["c"]["user"].put(f"{API}/jobs/{job_id}/finding-marks/{k}", json={"status": None}).json() is None
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/finding-marks").json()["marks"] == []
    actions = s["db"].scalars(select(AuditEvent.details).where(AuditEvent.action == "finding.marked")).all()
    assert [a["status"] for a in actions] == ["not_issue", "fixed", None]


def test_the_list_downloads_as_csv_with_the_statuses(setup):
    s = setup
    job_id = _done(s)
    items = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["result"]["review"]["findings"]
    s["c"]["user"].put(f"{API}/jobs/{job_id}/finding-marks/{items[0]['key']}", json={"status": "checked"})
    r = s["c"]["user"].get(f"{API}/jobs/{job_id}/findings.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="Budget_SUM-review.csv"' in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Severity", "Issue", "Page"] and rows[0][7] == "Status"
    assert rows[1][7] == "Checked" and rows[1][9] == "user@a.test" and rows[2][7] == ""
    assert len(rows) == 3  # both items, no "first N of M" line


def test_a_spreadsheet_formula_in_an_item_stays_text():
    out = finding_marks.findings_csv([{"severity": "review", "issue": "=HYPERLINK(\"x\")", "page": 1}], {}, 1)
    assert "'=HYPERLINK" in out
