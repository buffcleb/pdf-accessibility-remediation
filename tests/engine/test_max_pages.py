"""The engine refuses a document with more pages than MAX_PAGES at submission. It used to accept any count and
trust its caller's cap (the portal's 2,000); a document of tens of thousands of pages held a worker until the
time limit."""
import pikepdf
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from app.main import app
    from app.worker import tasks
    monkeypatch.setattr(tasks.remediate, "apply_async", lambda *a, **k: None)  # nothing is run; the answer is the test
    with TestClient(app) as c:
        yield c


def _pdf_with_pages(path, n: int):
    pdf = pikepdf.new()
    for _ in range(n):
        pdf.add_blank_page(page_size=(200, 100))
    pdf.save(path)


def test_a_document_past_the_page_cap_is_refused(client, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_pages", 2)
    _pdf_with_pages(tmp_path / "three.pdf", 3)
    before = set(get_settings().jobs_dir.glob("*"))
    with (tmp_path / "three.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("three.pdf", fh, "application/pdf")},
                        data={"ai_provider": "none"})
    assert r.status_code == 422 and "3 pages" in r.json()["detail"] and "MAX_PAGES" in r.json()["detail"]
    assert set(get_settings().jobs_dir.glob("*")) == before  # nothing of it is kept


def test_a_document_within_the_cap_and_a_cap_of_zero_are_accepted(client, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_pages", 2)
    _pdf_with_pages(tmp_path / "two.pdf", 2)
    with (tmp_path / "two.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("two.pdf", fh, "application/pdf")},
                        data={"ai_provider": "none"})
    assert r.status_code == 202, r.text
    monkeypatch.setattr(get_settings(), "max_pages", 0)
    _pdf_with_pages(tmp_path / "many.pdf", 5)
    with (tmp_path / "many.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("many.pdf", fh, "application/pdf")},
                        data={"ai_provider": "none"})
    assert r.status_code == 202, r.text
