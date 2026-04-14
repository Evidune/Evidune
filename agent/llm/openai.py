"""OpenAI-compatible client."""

from __future__ import annotations

from typing import Any

from agent.llm.base import LLMClient


class OpenAIClient(LLMClient):
    """OpenAI-compatible client (also works with servers that speak the
    OpenAI chat.completions API — e.g. Ollama, vLLM, OpenRouter).
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("Install openai: pip install aiflay[openai]") from e

        self.model = model
        self.temperature = temperature
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore
            temperature=kwargs.get("temperature", self.temperature),
        )
        return resp.choices[0].message.content or ""
