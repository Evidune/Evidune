"""Anthropic Claude client."""

from __future__ import annotations

from typing import Any

from agent.llm.base import LLMClient


class AnthropicClient(LLMClient):
    """Anthropic Claude client."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError("Install anthropic: pip install aiflay[anthropic]") from e

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = AsyncAnthropic(**kwargs)

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        # Anthropic separates system from user/assistant messages
        system_parts = []
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                chat_messages.append(msg)

        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            temperature=kwargs.get("temperature", self.temperature),
            system="\n\n".join(system_parts) if system_parts else "",
            messages=chat_messages,  # type: ignore
        )
        return resp.content[0].text if resp.content else ""
