"""Factory for building LLM clients by provider name."""

from __future__ import annotations

from typing import Any

from agent.llm.anthropic import AnthropicClient
from agent.llm.base import LLMClient
from agent.llm.codex import CodexClient
from agent.llm.local import LocalClient
from agent.llm.openai import OpenAIClient


def create_llm_client(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> LLMClient:
    """Build an LLMClient for the named provider."""
    if provider == "openai":
        return OpenAIClient(
            model=model, api_key=api_key, base_url=base_url, temperature=temperature
        )
    if provider == "anthropic":
        return AnthropicClient(model=model, api_key=api_key, temperature=temperature, **kwargs)
    if provider == "local":
        return LocalClient(
            model=model,
            base_url=base_url or "http://localhost:11434/v1",
            temperature=temperature,
        )
    if provider == "codex":
        return CodexClient(
            model=model,
            base_url=base_url,
            temperature=temperature,
            auth_path=kwargs.get("auth_path"),
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
