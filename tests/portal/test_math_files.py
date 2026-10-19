"""#403: a document with formulas gets the math files (the formula list and the MathML zip) beside its PDFs."""
from __future__ import annotations

import uuid

from portal.models import Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401


def test_the_math_files_are_kept_labelled_and_always_downloaded(setup):
    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    job_id = upload(s["c"]["user"], ("Calculus.pdf", pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(db)
    eng_id = db.get(Job, uuid.UUID(job_id)).engine_job_id
    s["engine"].complete(eng_id)
    base = f"/api/v1/jobs/{eng_id}/files"
    s["engine"].jobs[eng_id]["reports"].update(math_files=f"{base}/doc_math.zip", math_list=f"{base}/doc_formulas.html",
                                               math_nemeth=f"{base}/doc_math_nemeth.brf",
                                               math_ueb=f"{base}/doc_math_ueb.brf")
    run_worker(db)

    job = s["c"]["user"].get(f"{API}/jobs/{job_id}").json()
    files = {f["kind"]: f for f in job["files"]}
    assert files["math_list"]["label"] == "Math: every formula, to read with a screen reader (web page)"
    assert files["math_files"]["label"] == "Math: every formula (MathML files, zip)"
    assert (files["math_nemeth"]["label"], files["math_ueb"]["label"]) == ("Math in braille: Nemeth (BRF)",
                                                                           "Math in braille: UEB (BRF)")
    brf = s["c"]["user"].get(f"{API}/jobs/{job_id}/files/{files['math_ueb']['id']}")
    assert brf.headers["content-type"] == "application/x-brf"
    assert brf.headers["content-disposition"].startswith("attachment")
    r = s["c"]["user"].get(f"{API}/jobs/{job_id}/files/{files['math_list']['id']}")
    # The page is the document's text: downloaded, never shown on the portal's own site
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/files/{files['math_files']['id']}").headers[
        "content-type"] == "application/zip"
