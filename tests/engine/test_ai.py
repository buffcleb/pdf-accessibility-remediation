import json

import httpx
import pytest
import respx

from app.ai.base import AIError, parse_json_object
from app.ai.ollama import OllamaProvider
from app.ai.registry import available_providers, get_provider


def test_parse_json_lenient():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"alt": "x"} hope that helps') == {"alt": "x"}
    with pytest.raises(AIError):
        parse_json_object("no json here")


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_alt_text_and_usage(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "ai_max_retries", 1)
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(
                {"decorative": False, "kind": "chart", "alt": "Sales rose 20% in Q3", "latex": None})}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40}}),
    ]
    p = get_provider("openrouter", "openai/gpt-4o")
    async with p:
        res = await p.describe_image(b"\x89PNG", 0, "context")
    assert res["alt"] == "Sales rose 20% in Q3" and res["kind"] == "chart"
    assert p.usage.prompt_tokens == 900 and p.usage.completion_tokens == 40
    sent = json.loads(route.calls[-1].request.content)
    assert sent["model"] == "openai/gpt-4o"
    assert sent["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert route.calls[-1].request.headers["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
@respx.mock
async def test_ollama_reading_order_validation(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://ollama:11434")
    monkeypatch.setattr(get_settings(), "ai_max_retries", 0)
    respx.post("http://ollama:11434/api/chat").mock(return_value=httpx.Response(200, json={
        "message": {"content": '{"order": [2, 1]}'}, "prompt_eval_count": 10, "eval_count": 5}))
    p = OllamaProvider("llava")
    regions = [{"id": "a", "bbox": (0, 0, 1, 1), "text": "x"}, {"id": "b", "bbox": (0, 2, 1, 3), "text": "y"}]
    async with p:
        assert await p.determine_reading_order(b"png", 0, 100, 100, regions) == ["b", "a"]
        with pytest.raises(AIError):  # missing id -> rejected
            await p.determine_reading_order(b"png", 0, 100, 100, regions + [{"id": "c", "bbox": (0, 0, 1, 1), "text": ""}])
    assert p.usage.prompt_tokens == 20


def test_registry():
    assert {"none", "openrouter", "ollama", "localai", "mock"} <= set(available_providers())
    assert get_provider("none") is None
    with pytest.raises(ValueError):
        get_provider("nope")


def test_truncated_and_looping_output_is_salvaged():
    from app.ai.base import collapse_repeats

    data = parse_json_object('{"decorative": false, "kind": "text", "alt": "Policy Title: Policy Policy Title: Policy Policy Title: Policy Policy Title: Pol')
    assert data["_truncated"] and data["alt"].startswith("Policy Title")
    assert collapse_repeats("Policy Title: Policy Policy Title: Policy Policy Title: Policy Policy Title: Policy") .count("Policy Title") == 1
    assert collapse_repeats("A cat on a mat.") == "A cat on a mat."


def test_font_substitute_classification():
    from app.tagging.font_embed import classify

    assert classify("Times-Roman") == ("serif", False, False)
    assert classify("ABCDEF+TimesNewRomanPS-BoldItalicMT") == ("serif", True, True)
    assert classify("Arial,Bold") == ("sans", True, False)
    assert classify("Helvetica-Oblique") == ("sans", False, True)
    assert classify("CourierNewPSMT") == ("mono", False, False)
    assert classify("Calibri") is None and classify("Symbol") is None


@pytest.mark.asyncio
async def test_provider_usable_after_context_exit():
    from app.ai.mock import MockProvider

    p = MockProvider()
    async with p:
        await p.chat_json("Describe this image on page 1", [b"x"])
    async with p:  # second phase of a job (existing figures) reuses the provider
        assert (await p.chat_json("Describe this image on page 2", [b"x"]))["alt"]


@pytest.mark.asyncio
@respx.mock
async def test_ollama_think_off_and_answer_in_thinking_field(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://ollama:11434")
    monkeypatch.setattr(get_settings(), "ollama_think", "off")
    route = respx.post("http://ollama:11434/api/chat").mock(return_value=httpx.Response(200, json={
        "message": {"content": "", "thinking": '{"decorative": false, "kind": "photo", "alt": "A dog", "latex": null}'},
        "prompt_eval_count": 5, "eval_count": 5}))
    p = OllamaProvider("qwen3-vl:8b")
    async with p:
        res = await p.describe_image(b"png", 0, "")
    assert res["alt"] == "A dog"
    assert json.loads(route.calls[0].request.content)["think"] is False


@pytest.mark.asyncio
async def test_placeholder_alt_is_rejected():
    from app.ai.mock import MockProvider

    class Placeholder(MockProvider):
        async def _chat(self, model, system, prompt, images):
            from app.ai.base import ChatResult
            return ChatResult('{"decorative": false, "kind": "formula", "alt": "No text available", "latex": null}')

    async with Placeholder() as p:
        with pytest.raises(AIError):
            await p.describe_image(b"x", 0, "")


@pytest.mark.asyncio
@respx.mock
async def test_preflight_reports_unreachable_and_missing_models(monkeypatch):
    from app.ai.localai import LocalAIProvider
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "localai_base_url", "http://localai.test/v1")
    respx.get("http://localai.test/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    async with LocalAIProvider("qwen3-vl-8b-instruct") as p:
        with pytest.raises(AIError, match="cannot reach"):
            await p.preflight()

    respx.get("http://localai.test/v1/models").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "gemma-4-26b-a4b-it-qat-q4_0"}]}))
    async with LocalAIProvider("qwen3-vl-8b-instruct") as p:
        with pytest.raises(AIError, match="qwen3-vl-8b-instruct"):
            await p.preflight()
    async with LocalAIProvider("gemma-4-26b-a4b-it-qat-q4_0") as p:
        await p.preflight()  # present: passes


@pytest.mark.asyncio
@respx.mock
async def test_ollama_preflight_accepts_latest_tag(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://ollama:11434")
    respx.get("http://ollama:11434/api/tags").mock(return_value=httpx.Response(200, json={
        "models": [{"name": "gemma4:26b"}, {"name": "qwen3-vl:latest"}]}))
    async with OllamaProvider("qwen3-vl") as p:
        await p.preflight()


@pytest.mark.asyncio
@respx.mock
async def test_localai_disables_thinking_and_reads_reasoning_fallback(monkeypatch):
    from app.ai.localai import LocalAIProvider
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "localai_base_url", "http://localai.test/v1")
    route = respx.post("http://localai.test/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning":
                     '{"decorative": false, "kind": "photo", "alt": "A turtle", "latex": null}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 9}}))
    async with LocalAIProvider("gemma-4-26b-a4b-it-qat-q4_0") as p:
        res = await p.describe_image(b"png", 0, "")
    assert res["alt"] == "A turtle"
    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "none"
