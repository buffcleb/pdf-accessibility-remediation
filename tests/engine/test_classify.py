"""The kind-of-document classifier: what it reads (text and hints, never images), how it asks Jev and a gateway
model, and the API. No real model is called."""
from __future__ import annotations

import asyncio
import json

import fitz
import httpx
import pytest

from app import classify as cl
from app.config import get_settings

KINDS = [{"key": "math-and-science", "label": "Math and science", "description": "Equations and formulas"},
         {"key": "forms", "label": "Forms", "description": "Forms to fill in"}]


@pytest.fixture()
def math_pdf(tmp_path):
    doc = fitz.open()
    for i in range(30):
        page = doc.new_page()
        text = (f"Chapter {i}. Let ∑ aᵢ ≤ ∫ f(x) dx for every α ∈ ℝ and β ≥ 0." if i else "A thesis in analysis")
        page.insert_text((72, 72), text, fontname="helv", fontsize=11)
    doc.set_metadata({"title": "Quantitative estimates"})
    path = tmp_path / "thesis.pdf"
    doc.save(path)
    return path


def test_pages_read_are_the_first_and_some_spread_through():
    assert cl._pages_to_read(1, 3) == [0]
    assert cl._pages_to_read(3, 3) == [0, 1, 2]
    assert cl._pages_to_read(10, 3) == [0, 1, 6]
    assert cl._pages_to_read(109, 3) == [0, 1, 28, 55, 82]


def test_the_sample_has_text_and_hints(math_pdf):
    s = cl.sample(math_pdf, 3, "thesis.pdf")
    assert s["pages"] == 30 and s["pages_read"] == [1, 2, 10, 17, 24]
    assert s["title"] == "Quantitative estimates" and "[page 1]" in s["text"] and "Chapter" in s["text"]
    assert s["hints"]["math symbols"] > 0
    assert len(s["text"]) <= cl.MAX_CHARS


def _mock_client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(cl.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_jev_is_asked_a_choice_with_general_first(math_pdf, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "typesafe/jev-1.13", "usage": {"input_tokens": 900, "cost": 0.00004},
            "answers": {"kind": {"type": "choice", "choice": "math-and-science", "confidence": 0.97,
                                 "probabilities": {"general": 0.02, "math-and-science": 0.98, "forms": 0.0}}}})

    _mock_client(monkeypatch, handler)
    r = asyncio.run(cl.classify(math_pdf, KINDS, "jev", filename="thesis.pdf"))
    assert seen["url"].endswith("/systemone") and seen["auth"] == "Bearer test-key"
    q = seen["body"]["questions"]["kind"]
    assert seen["body"]["model"] == "jev-1.13" and q["type"] == "choice"
    assert list(q["criteria"]) == ["general", "math-and-science", "forms"]
    assert seen["body"]["state"]["file name"] == "thesis.pdf"
    assert r["kind"] == "math-and-science" and r["confidence"] == 0.97 and r["cost_usd"] == 0.00004
    assert r["backend"] == "jev" and r["hints"]["math symbols"] > 0


def test_an_answer_that_isnt_offered_is_dropped(math_pdf, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json={
        "answers": {"kind": {"choice": "poetry", "confidence": 1.0}}}))
    r = asyncio.run(cl.classify(math_pdf, KINDS, "jev"))
    assert r["kind"] is None and r["reason"] == "not_offered"


def test_jev_needs_a_key_and_errors_are_ai_errors(math_pdf, monkeypatch):
    from app.ai.base import AIError
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    with pytest.raises(AIError):
        asyncio.run(cl.classify(math_pdf, KINDS, "jev"))
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "k")
    _mock_client(monkeypatch, lambda req: httpx.Response(503, text="busy"))
    with pytest.raises(AIError):
        asyncio.run(cl.classify(math_pdf, KINDS, "jev"))


def test_the_gateway_model_is_asked_for_json(math_pdf, monkeypatch):
    prompts = []

    async def chat_json(self, prompt, images=None, model=None):
        prompts.append((self.model, prompt, images))
        return {"kind": "Forms", "confidence": "0.8"}

    from app.ai.gateway import GatewayProvider
    monkeypatch.setattr(GatewayProvider, "chat_json", chat_json)
    r = asyncio.run(cl.classify(math_pdf, KINDS, "gateway", "local-vision"))
    model, prompt, images = prompts[0]
    assert model == "local-vision" and not images and "- general: General" in prompt and "- forms:" in prompt
    assert r["kind"] == "forms" and r["confidence"] == 0.8 and r["backend"] == "gateway"


def test_no_text_means_no_call(tmp_path, monkeypatch):
    doc = fitz.open()
    doc.new_page()
    path = tmp_path / "scan.pdf"
    doc.save(path)
    _mock_client(monkeypatch, lambda req: pytest.fail("no call expected"))
    r = asyncio.run(cl.classify(path, KINDS, "jev"))
    assert r["kind"] is None and r["reason"] == "no_text"


def test_the_api(math_pdf, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def fake(path, kinds, backend, model=None, pages=3, filename=""):
        return {"kind": "forms", "confidence": 0.9, "backend": backend, "seen": [k["key"] for k in kinds],
                "pages": pages, "filename": filename}

    monkeypatch.setattr(cl, "classify", fake)
    with TestClient(app) as c, math_pdf.open("rb") as fh:
        r = c.post("/api/v1/classify", files={"file": ("t.pdf", fh, "application/pdf")},
                   data={"kinds": json.dumps(KINDS), "backend": "jev", "pages": "4"})
        assert r.status_code == 200, r.text
        assert r.json() == {"kind": "forms", "confidence": 0.9, "backend": "jev",
                            "seen": ["math-and-science", "forms"], "pages": 4, "filename": "t.pdf"}
        assert c.post("/api/v1/classify", files={"file": ("t.pdf", b"%PDF-1.4", "application/pdf")},
                      data={"kinds": "[]", "backend": "other"}).status_code == 422
        assert c.post("/api/v1/classify", files={"file": ("t.pdf", b"%PDF-1.4", "application/pdf")},
                      data={"kinds": "{", "backend": "jev"}).status_code == 422
