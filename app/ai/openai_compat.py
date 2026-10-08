"""OpenAI-compatible /chat/completions adapter (OpenRouter, LocalAI, vLLM, etc.)."""
from __future__ import annotations

import httpx

from app.ai.base import AIError, AIProvider, ChatResult, TokenUsage


class OpenAICompatibleProvider(AIProvider):
    name = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    json_mode: bool = True
    reasoning_effort: str = ""  # sent as "reasoning_effort" when set (LocalAI: "none" disables thinking)
    send_metadata: bool = False  # send self.tags as LiteLLM metadata/tags, for spend per job

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def preflight(self) -> None:
        url = f"{self.base_url.rstrip('/')}/models"
        try:
            resp = await self.client.get(url, headers=self.headers(), timeout=15)
        except httpx.TransportError as exc:
            raise AIError(f"cannot reach {self.name} at {self.base_url}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise AIError(f"{self.name} at {self.base_url} rejected the API key (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise AIError(f"{self.name} at {self.base_url} answered HTTP {resp.status_code} for /models")
        try:
            ids = [m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)]
        except ValueError:
            ids = []
        self._check_models([i for i in ids if i], self.base_url)

    async def _chat(self, model: str, system: str, prompt: str, images: list[bytes]) -> ChatResult:
        content: list[dict] | str
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.b64(img)}"}} for img in images
            ]
        else:
            content = prompt
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.send_metadata and self.tags:
            body["metadata"] = {"tags": [f"{k}:{v}" for k, v in sorted(self.tags.items())], **self.tags}
            if self.tags.get("user"):
                body["user"] = self.tags["user"]
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        resp = await self.client.post(url, json=body, headers=self.headers())
        for field in ("response_format", "reasoning_effort"):
            if resp.status_code == 400 and field in body and field.split("_")[0] in resp.text.lower():
                # Some servers/models reject an optional field ("Reasoning is mandatory for this endpoint and
                # cannot be disabled"); retry once without it.
                body.pop(field)
                resp = await self.client.post(url, json=body, headers=self.headers())
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise AIError(str(data["error"]))
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise AIError(f"unexpected response shape: {str(data)[:200]}") from exc
        text = message.get("content") or ""
        if isinstance(text, str) and not text.strip():
            # Thinking models may put everything in a reasoning field; the JSON may still be there.
            text = message.get("reasoning") or message.get("reasoning_content") or ""
            if not text and data["choices"][0].get("finish_reason") == "length":
                raise AIError("model used its whole output allowance before answering (thinking?); "
                              "set LOCALAI_REASONING_EFFORT=none or raise AI_MAX_OUTPUT_TOKENS")
        if isinstance(text, list):  # some providers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        u = data.get("usage") or {}
        try:  # LiteLLM reports what each call cost
            cost = float(resp.headers.get("x-litellm-response-cost") or 0)
        except ValueError:
            cost = 0.0
        return ChatResult(text, TokenUsage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cost))
