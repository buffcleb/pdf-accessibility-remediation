"""Ollama native /api/chat adapter (images passed as base64 in `images`)."""
from __future__ import annotations

import httpx

from app.ai.base import AIError, AIProvider, ChatResult, TokenUsage
from app.config import get_settings


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, **kw):
        s = get_settings()
        self.base_url = s.ollama_base_url.rstrip("/")
        self.num_ctx = s.ollama_num_ctx
        self.think = s.ollama_think.lower()
        model = model or s.ollama_default_model
        super().__init__(model, table_model=s.ollama_table_model or model, **kw)

    async def preflight(self) -> None:
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags", timeout=15)
            resp.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise AIError(f"cannot reach Ollama at {self.base_url}: {exc}") from exc
        self._check_models([m.get("name") for m in resp.json().get("models", []) if m.get("name")], self.base_url)

    async def _chat(self, model: str, system: str, prompt: str, images: list[bytes]) -> ChatResult:
        msg: dict = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [self.b64(i) for i in images]
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, msg],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": self.max_output_tokens, "repeat_penalty": 1.2},
        }
        if self.num_ctx:
            body["options"]["num_ctx"] = self.num_ctx
        if self.think in ("off", "on"):
            body["think"] = self.think == "on"
        resp = await self.client.post(f"{self.base_url}/api/chat", json=body)
        if resp.status_code == 400 and "think" in resp.text.lower() and "think" in body:
            body.pop("think")  # model without a thinking switch
            resp = await self.client.post(f"{self.base_url}/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise AIError(str(data["error"]))
        msg = data.get("message") or {}
        # Some models (qwen3-vl) put the whole answer in "thinking" when thinking is off.
        text = (msg.get("content") or "").strip() or (msg.get("thinking") or "")
        return ChatResult(text, TokenUsage(int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)))
