"""Local HTTP endpoint client (Ollama, vLLM, LMStudio, etc.)."""

from __future__ import annotations

from typing import Any

from agent.llm.base import LLMClient
from agent.llm.openai import OpenAIClient


class LocalClient(LLMClient):
    """Thin wrapper around OpenAIClient pointed at a local base_url."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434/v1",
        temperature: float = 0.7,
    ) -> None:
        self._inner = OpenAIClient(
            model=model,
            api_key="not-needed",
            base_url=base_url,
            temperature=temperature,
        )

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return await self._inner.complete(messages, **kwargs)
