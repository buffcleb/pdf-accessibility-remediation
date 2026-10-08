"""The AI gateway (LiteLLM): one OpenAI-compatible endpoint in front of every model the service offers,
local or cloud. Models are LiteLLM model names chosen per job by the portal's AI tiers; each call carries
the job and tier as tags so LiteLLM can report spend per job."""
from __future__ import annotations

import httpx

from app.ai.base import AIError
from app.ai.openai_compat import OpenAICompatibleProvider
from app.config import get_settings


class GatewayProvider(OpenAICompatibleProvider):
    name = "gateway"
    send_metadata = True

    def __init__(self, model: str | None = None, **kw):
        s = get_settings()
        self.base_url = s.gateway_base_url
        self.api_key = s.gateway_api_key
        self.json_mode = s.gateway_json_mode
        self.reasoning_effort = s.gateway_reasoning_effort
        super().__init__(model or s.gateway_default_model, table_model=s.gateway_table_model or None, **kw)


async def list_models() -> list[str]:
    """Model names the gateway serves (for the portal's AI tier picker)."""
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.gateway_api_key}"} if s.gateway_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{s.gateway_base_url.rstrip('/')}/models", headers=headers)
    except httpx.TransportError as exc:
        raise AIError(f"cannot reach the AI gateway at {s.gateway_base_url}: {exc}") from exc
    if resp.status_code >= 400:
        raise AIError(f"the AI gateway answered HTTP {resp.status_code}")
    return sorted({m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict) and m.get("id")})
