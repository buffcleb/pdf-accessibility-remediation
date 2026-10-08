from __future__ import annotations

from app.ai.openai_compat import OpenAICompatibleProvider
from app.config import get_settings


class LocalAIProvider(OpenAICompatibleProvider):
    name = "localai"
    json_mode = False  # LocalAI's grammar-constrained json mode varies by backend; we parse leniently instead.

    def __init__(self, model: str | None = None, **kw):
        s = get_settings()
        self.base_url = s.localai_base_url
        self.api_key = s.localai_api_key
        self.reasoning_effort = s.localai_reasoning_effort
        super().__init__(model or s.localai_default_model, **kw)
