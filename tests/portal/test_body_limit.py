"""A request whose declared size is past what its route's files may add up to is refused before any of it is read
(before sign-in is even checked), so an account can't fill the API pod's temporary space with bodies far larger
than the file-size and file-count limits, which the routes could only enforce after the whole body was spooled."""
import pytest

from tests.portal.test_jobs import API, fake_engine, pdf_bytes, setup, upload  # noqa: F401

MB = 1024 * 1024


@pytest.fixture
def small_limits(setup):
    s = setup
    r = s["c"]["super"].patch(f"{API}/admin/settings",
                              json={"max_file_mb": 1, "max_files_per_upload": 2, "help_max_request_mb": 1})
    assert r.status_code == 200, r.text
    return s


def _big(n_mb: float) -> bytes:
    return b"%PDF-1.7\n" + b"0" * int(n_mb * MB)


def test_a_body_past_the_route_limit_is_refused_before_anything_else(small_limits, make_client):
    s = small_limits
    anonymous = make_client()
    # 2 files of 1 MB plus 1 MB of slack is the cap; 4 MB of body is past it, and not even sign-in is consulted
    r = anonymous.post(f"{API}/jobs", files=[("files", ("d.pdf", _big(4), "application/pdf"))])
    assert r.status_code == 413 and r.json()["detail"]["error"] == "request_too_large"
    assert "3 MB" in r.json()["detail"]["message"]
    r = s["c"]["user"].post(f"{API}/jobs", files=[("files", ("d.pdf", _big(4), "application/pdf"))])
    assert r.status_code == 413
    # Help attachments have their own request limit, applied before the request's job is looked up
    r = anonymous.post(f"{API}/jobs/00000000-0000-0000-0000-000000000000/help",
                       data={"body": "x"}, files=[("files", ("big.png", _big(3), "image/png"))])
    assert r.status_code == 413
    r = anonymous.post(f"{API}/help/00000000-0000-0000-0000-000000000000/corrected",
                       files=[("file", ("d.pdf", _big(3), "application/pdf"))])
    assert r.status_code == 413


def test_a_body_within_the_limit_reaches_the_route(small_limits):
    s = small_limits
    r = upload(s["c"]["user"], ("d.pdf", pdf_bytes(1)))
    assert r.status_code == 200, r.text
    # Still past the per-file cap once read, and that is the route's own answer, not the size check's
    r = s["c"]["user"].post(f"{API}/jobs", files=[("files", ("d.pdf", _big(1.5), "application/pdf"))])
    assert r.status_code == 200 and r.json()[0]["error"]["error"] == "file_too_large"
    # Other POSTs with a body are untouched
    r = s["c"]["super"].patch(f"{API}/admin/settings", json={"max_file_mb": 1})
    assert r.status_code == 200
