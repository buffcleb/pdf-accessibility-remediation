from __future__ import annotations

from app.ai.base import AIError
from app.ai.openai_compat import OpenAICompatibleProvider
from app.config import get_settings


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, model: str | None = None, **kw):
        s = get_settings()
        if not s.openrouter_api_key:
            raise AIError("OPENROUTER_API_KEY is not configured")
        self.base_url = s.openrouter_base_url
        self.api_key = s.openrouter_api_key
        # When the caller pins a model we use it for everything; otherwise tables get the stronger text model.
        table_model = model or s.openrouter_table_model
        super().__init__(model or s.openrouter_default_model, table_model=table_model, **kw)

    def headers(self) -> dict[str, str]:
        h = super().headers()
        h["X-Title"] = "PDF Accessibility Remediation"
        return h
