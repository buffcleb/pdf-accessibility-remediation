from __future__ import annotations

from pathlib import Path

from app.ai.base import AIProvider
from app.config import get_settings


def available_providers() -> list[str]:
    names = ["none", "gateway", "openrouter", "ollama", "localai"]
    if get_settings().allow_mock_provider:
        names.append("mock")
    return names


def get_provider(name: str, model: str | None = None, log_path: Path | None = None) -> AIProvider | None:
    """Return a provider instance, or None for 'none' (heuristics only, no AI)."""
    name = (name or "none").lower()
    if name == "none":
        return None
    if name == "gateway":
        from app.ai.gateway import GatewayProvider
        return GatewayProvider(model, log_path=log_path)
    if name == "openrouter":
        from app.ai.openrouter import OpenRouterProvider
        return OpenRouterProvider(model, log_path=log_path)
    if name == "ollama":
        from app.ai.ollama import OllamaProvider
        return OllamaProvider(model, log_path=log_path)
    if name == "localai":
        from app.ai.localai import LocalAIProvider
        return LocalAIProvider(model, log_path=log_path)
    if name == "mock" and get_settings().allow_mock_provider:
        from app.ai.mock import MockProvider
        return MockProvider(model, log_path=log_path)
    raise ValueError(f"unknown AI provider: {name}")
