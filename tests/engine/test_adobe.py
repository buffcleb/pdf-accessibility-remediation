"""Adobe Autotag route, tested only against the fake Adobe server (app.adobe.fake): no Adobe account,
no network."""
import asyncio
import json
import threading

import httpx
import pikepdf
import pymupdf as fitz
import pytest

from app.adobe import chunks, client as client_mod
from app.adobe.client import AdobeClient, AdobeError
from app.adobe.fake import FakeBehavior, create_app
from app.adobe.limiter import MemoryLimiter
from app.config import get_settings
from app.verification.integrity import check_integrity


async def _nosleep(_s):
    await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def adobe_settings(monkeypatch):
    s = get_settings()
    for k, v in {"adobe_client_id": "fake-id", "adobe_client_secret": "fake-secret", "adobe_base_url": "http://adobe.test",
                 "adobe_poll_seconds": 0.0, "adobe_max_retries": 3, "adobe_generate_report": True,
                 "adobe_enabled": False, "adobe_max_pages_per_call": 100, "adobe_max_mb_per_call": 100}.items():
        monkeypatch.setattr(s, k, v)
    client_mod._tokens.clear()


def _client(app, **kw) -> AdobeClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adobe.test")
    return AdobeClient(http=http, limiter=MemoryLimiter(1000, 4, 600, sleep=_nosleep), sleep=_nosleep, **kw)


@pytest.fixture()
def combo(samples, tmp_path):
    """A five-page document with a bookmark outline and an internal link, built from the samples."""
    doc = fitz.open()
    for n in ("simple_report", "two_column", "table_doc", "partially_tagged"):
        doc.insert_pdf(fitz.open(samples / f"{n}.pdf"))
    doc[0].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(50, 50, 200, 70), "page": doc.page_count - 1})
    doc.set_toc([[1, "Start", 1], [1, "End", doc.page_count]])
    path = tmp_path / "combo.pdf"
    doc.save(path)
    return path


# --- client -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autotag_round_trip_cleans_up(tmp_path, samples):
    app = create_app(tmp_path / "fake", FakeBehavior(polls=2))
    async with _client(app) as c:
        res = await c.autotag(samples / "simple_report.pdf", tmp_path, "out")
    with pikepdf.open(res.tagged_pdf) as pdf:
        assert "/StructTreeRoot" in pdf.Root
        assert not check_integrity(pdf)[0]
    assert res.report is not None and res.report.read_bytes()[:2] == b"PK"
    st = app.state.fake
    assert st.calls["token"] == 1 and st.calls["status"] == 3
    assert len(st.deleted) == 3  # the upload, the tagged PDF and the report


@pytest.mark.asyncio
async def test_throttling_is_retried_and_shared(tmp_path, samples):
    app = create_app(tmp_path / "fake", FakeBehavior(throttle_every=3))
    lim = MemoryLimiter(1000, 4, 600, sleep=_nosleep)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adobe.test")
    async with AdobeClient(http=http, limiter=lim, sleep=_nosleep) as c:
        res = await c.autotag(samples / "table_doc.pdf", tmp_path, "out")
    assert res.retries >= 1 and res.tagged_pdf.exists()
    assert lim._pause_until > 0  # a 429 with Retry-After pauses every worker, not just this one


@pytest.mark.asyncio
async def test_out_of_transactions_is_not_retried(tmp_path, samples):
    app = create_app(tmp_path / "fake", FakeBehavior(quota=True))
    async with _client(app) as c:
        with pytest.raises(AdobeError) as e:
            await c.autotag(samples / "table_doc.pdf", tmp_path, "out")
    assert e.value.code == "adobe_quota" and not e.value.retryable
    assert app.state.fake.api_requests == 1


@pytest.mark.asyncio
async def test_unreadable_pdf_is_a_permanent_failure(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is not a pdf\n")
    app = create_app(tmp_path / "fake", FakeBehavior())
    async with _client(app) as c:
        with pytest.raises(AdobeError) as e:
            await c.autotag(bad, tmp_path, "out")
    assert e.value.code == "CORRUPT_DOCUMENT" and not e.value.retryable


@pytest.mark.asyncio
async def test_expired_token_is_refreshed(tmp_path, samples):
    app = create_app(tmp_path / "fake", FakeBehavior())
    async with _client(app) as c:
        await c.upload(samples / "table_doc.pdf")
        app.state.fake.tokens.clear()  # Adobe forgot the token
        await c.upload(samples / "table_doc.pdf")
    assert app.state.fake.calls["token"] == 2


@pytest.mark.asyncio
async def test_concurrency_slots_are_shared():
    lim = MemoryLimiter(1000, 2, 600, sleep=_nosleep)
    inside, peak = 0, 0

    async def job():
        nonlocal inside, peak
        async with lim.slot():
            inside += 1
            peak = max(peak, inside)
            for _ in range(5):
                await asyncio.sleep(0)
            inside -= 1

    await asyncio.gather(*(job() for _ in range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_requests_per_minute():
    now = [0.0]

    async def sleep(s):
        now[0] += s

    lim = MemoryLimiter(3, 1, 600, clock=lambda: now[0], sleep=sleep)
    for _ in range(7):
        await lim.request()
    assert 120 <= now[0] < 180  # 3 in the first minute, 3 in the second, the 7th in the third


# --- split and merge --------------------------------------------------------------------------

def test_small_document_is_sent_whole(samples, tmp_path):
    parts = chunks.split(samples / "simple_report.pdf", tmp_path, 100, 100)
    assert len(parts) == 1 and parts[0].path == samples / "simple_report.pdf"


def test_split_by_pages_and_size(combo, tmp_path):
    parts = chunks.split(combo, tmp_path, 2, 100)
    assert [(p.first, p.pages) for p in parts] == [(0, 2), (2, 2), (4, 1)]
    for p in parts:
        with pikepdf.open(p.path) as pdf:
            assert len(pdf.pages) == p.pages and "/StructTreeRoot" not in pdf.Root
    tiny = chunks.split(combo, tmp_path, 100, combo.stat().st_size / 1024 / 1024 * 0.6)
    assert len(tiny) >= 2 and sum(p.pages for p in tiny) == 5


@pytest.mark.asyncio
async def test_merged_parts_match_tagging_the_whole_document(combo, tmp_path):
    from app.adobe.fake import fake_autotag

    app = create_app(tmp_path / "fake", FakeBehavior())
    parts = chunks.split(combo, tmp_path, 2, 100)
    async with _client(app) as c:
        results = await asyncio.gather(*(c.autotag(p.path, tmp_path, f"t{p.first}") for p in parts))
    merged = tmp_path / "merged.pdf"
    assert chunks.merge(combo, [(p, r.tagged_pdf) for p, r in zip(parts, results)], merged) == []
    whole = tmp_path / "whole.pdf"
    fake_autotag(combo, whole)

    with pikepdf.open(merged) as m, pikepdf.open(whole) as w:
        assert not check_integrity(m)[0]
        _, mi = check_integrity(m)
        _, wi = check_integrity(w)
        assert len(mi.elements) == len(wi.elements)
        pages = {p.obj.objgen for p in m.pages}
        for e in mi.elements:  # every page reference points into the merged document's own pages
            pg = e.obj.get("/Pg")
            assert pg is None or pg.objgen in pages
        assert int(m.Root.StructTreeRoot.ParentTreeNextKey) >= len(m.pages)
    md, wd = fitz.open(merged), fitz.open(whole)
    assert md.get_toc() == [[1, "Start", 1], [1, "End", 5]]  # bookmarks and links survive the round trip
    assert any(link.get("page") == 4 for link in md[0].get_links())
    for i in range(md.page_count):
        assert md[i].get_pixmap(dpi=40).samples == wd[i].get_pixmap(dpi=40).samples


# --- pipeline and API -------------------------------------------------------------------------

@pytest.fixture()
def fake_adobe(monkeypatch, tmp_path):
    """Route the engine's Adobe client to the fake Adobe server."""
    from app.pipeline import adobe_job

    app = create_app(tmp_path / "fake", FakeBehavior())
    monkeypatch.setattr(get_settings(), "adobe_enabled", True)
    monkeypatch.setattr(adobe_job, "AdobeClient", lambda: _client(app))
    return app


@pytest.fixture()
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.worker import tasks

    def inline(task):
        def run(args, task_id=None, **kw):
            t = threading.Thread(target=lambda: _swallow(task.apply, args=args, task_id=task_id))
            t.start()
            t.join()
        return run

    monkeypatch.setattr(tasks.remediate, "apply_async", inline(tasks.remediate))
    monkeypatch.setattr(tasks.remediate_adobe, "apply_async", inline(tasks.remediate_adobe))
    with TestClient(app) as c:
        yield c


def _swallow(fn, **kw):
    try:
        fn(**kw)
    except Exception:  # noqa: BLE001
        pass


def test_adobe_route_is_refused_when_switched_off(api, samples):
    with (samples / "table_doc.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("t.pdf", fh, "application/pdf")}, data={"route": "adobe"})
    assert r.status_code == 422 and "adobe" in r.text


def test_adobe_route_end_to_end(api, samples, fake_adobe, monkeypatch):
    monkeypatch.setattr(get_settings(), "adobe_max_pages_per_call", 1)  # two parts, merged
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                     data={"route": "adobe", "ai_provider": "none"})
    assert r.status_code == 202, r.text
    body = api.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "completed", body
    m = body["metrics"]
    assert m["route"] == "adobe" and m["adobe"]["parts"] == 2 and m["adobe"]["pages_sent"] == 2
    assert body["files"]["remediated_primary"].endswith("_adobe_tagged.pdf")
    assert body["files"]["remediated_visual_fallback"] is None
    assert len(body["reports"]["adobe_reports"]) == 2
    assert body["reports"]["tag_verification"]["primary"]["integrity"]["passed"] is True
    assert set(body["reports"]["tag_maps"]) == {"original", "primary"}
    assert api.get(body["files"]["remediated_primary"]).content.startswith(b"%PDF")
    xlsx = api.get(body["reports"]["adobe_reports"][0])
    assert xlsx.status_code == 200 and "spreadsheetml" in xlsx.headers["content-type"]
    md = api.get(f"/api/v1/jobs/{r.json()['job_id']}?format=markdown")
    assert md.status_code == 200 and "Adobe" in md.text


def test_adobe_failure_is_reported(api, samples, fake_adobe):
    fake_adobe.state.behavior.quota = True
    with (samples / "table_doc.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("t.pdf", fh, "application/pdf")}, data={"route": "adobe"})
    body = api.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "failed"
    assert body["error"]["error"] == "adobe_quota" and body["error"]["retryable"] is False


def test_report_only_never_goes_to_adobe(api, samples, fake_adobe):
    with (samples / "table_doc.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("t.pdf", fh, "application/pdf")},
                     data={"route": "adobe", "flags": json.dumps({"report_only": True})})
    assert r.status_code == 422


def test_adobe_with_ai_alt_text_keeps_adobes_file_and_adds_a_copy(api, samples, fake_adobe):
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                     data={"route": "adobe", "ai_provider": "mock"})
    body = api.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "completed", body
    files, reps, m = body["files"], body["reports"], body["metrics"]
    assert files["remediated_primary"].endswith("_adobe_tagged.pdf")
    assert files["enhanced"].endswith("_adobe_improved.pdf")
    assert m["images_described"] >= 1 and m["ai_calls"] >= 1
    tv = reps["tag_verification"]
    assert tv["primary"]["integrity"]["passed"] and tv["enhanced"]["integrity"]["passed"]
    assert set(reps["tag_maps"]) == {"original", "primary", "enhanced"}

    def figures(ref):
        els = api.get(ref).json()["elements"]
        return [e for e in els if e["role"] == "Figure"]

    adobe_figs, ai_figs = figures(reps["tag_maps"]["primary"]), figures(reps["tag_maps"]["enhanced"])
    assert adobe_figs and not any(e.get("alt") for e in adobe_figs)  # Adobe's file is untouched
    assert ai_figs and all(e.get("alt") for e in ai_figs)
    assert "after_enhanced" in reps["comparison"]


def test_adobe_ai_unavailable_still_returns_adobes_file(api, samples, fake_adobe, monkeypatch):
    from app.ai.base import AIError
    from app.ai.mock import MockProvider

    async def down(self):
        raise AIError("gateway down")

    monkeypatch.setattr(MockProvider, "preflight", down)
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = api.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                     data={"route": "adobe", "ai_provider": "mock"})
    body = api.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "completed"
    assert body["metrics"]["ai_failures"] == 1 and body["metrics"]["images_described"] == 0
    assert any("every AI call failed" in w for w in body["warnings"])
