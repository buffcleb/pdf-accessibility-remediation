"""One save or draft of tag edits is bounded as a whole. Each edit had its own cap, but 5,000 of them at 64 kB each
is hundreds of megabytes, kept in the database for every job a person has."""
from tests.portal.test_jobs import API, fake_engine, setup  # noqa: F401 - fixtures
from tests.portal.test_versions import _completed_job

A_LOT = [{"op": "assign", "element": 1, "pieces": [{"page": 1, "mcid": m} for m in range(2000)]}] * 200  # about 10 MB, each edit under its own cap


def test_edits_that_add_up_to_too_much_are_refused(setup):
    s = setup
    job_id = _completed_job(s)
    r = s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": A_LOT})
    assert r.status_code == 413 and r.json()["detail"]["error"] == "edits_too_large"
    r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": A_LOT})
    assert r.status_code == 413
    # The same edits, fewer of them, are fine
    r = s["c"]["user"].put(f"{API}/jobs/{job_id}/tag-draft", json={"based_on": 1, "edits": A_LOT[:20]})
    assert r.status_code == 200
