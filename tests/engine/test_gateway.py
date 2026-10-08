"""The AI gateway provider: OpenAI-compatible calls to LiteLLM, tagged per job, with each call's cost."""
import httpx
import pytest
import respx

from app.ai.gateway import GatewayProvider, list_models
from app.config import get_settings


@pytest.fixture
def gw(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gateway_base_url", "http://gw.test/v1")
    monkeypatch.setattr(s, "gateway_api_key", "sk-test")
    return s


@respx.mock
@pytest.mark.asyncio
async def test_gateway_tags_calls_and_adds_up_cost(gw):
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.0125"}, json={
            "choices": [{"message": {"content": '{"alt": "A bar chart", "decorative": false}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    respx.post("http://gw.test/v1/chat/completions").mock(side_effect=answer)
    p = GatewayProvider("cloud-vision")
    p.tags = {"job": "j-1", "tier": "ai:cloud"}
    async with p:
        await p.chat_json("describe", [b"png"])
        await p.chat_json("describe again")
    import json
    body = json.loads(seen[0].content)
    assert body["model"] == "cloud-vision"
    assert body["metadata"]["tags"] == ["job:j-1", "tier:ai:cloud"] and body["metadata"]["job"] == "j-1"
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    assert p.usage.prompt_tokens == 200 and round(p.usage.cost_usd, 4) == 0.025


@respx.mock
@pytest.mark.asyncio
async def test_gateway_model_list(gw):
    respx.get("http://gw.test/v1/models").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "local-vision"}, {"id": "commercial"}, {"id": "local-vision"}]}))
    assert await list_models() == ["commercial", "local-vision"]


@respx.mock
@pytest.mark.asyncio
async def test_model_that_insists_on_reasoning_is_retried_without_turning_it_off(gw):
    import json
    bodies = []

    def answer(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "litellm.BadRequestError: OpenrouterException - "
                                                       "Reasoning is mandatory for this endpoint and cannot be disabled."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"alt": "A chart"}'}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    respx.post("http://gw.test/v1/chat/completions").mock(side_effect=answer)
    p = GatewayProvider("commercial")
    async with p:
        assert (await p.chat_json("describe"))["alt"] == "A chart"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]
