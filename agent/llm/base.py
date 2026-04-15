"""LLMClient abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent.tools.base import CompletionResult, Tool


class LLMClient(ABC):
    """Base class for LLM providers.

    All clients are async. `complete` returns the final response text —
    use this for simple text-only turns.

    Providers that implement tool-use expose `complete_with_tools`,
    which returns a CompletionResult carrying either a final `text` or
    a list of `tool_calls` the agent should execute.
    """

    @abstractmethod
    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        """Send messages to the LLM and return the response text."""
        ...

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        **kwargs: Any,
    ) -> CompletionResult:
        """Send messages + tools to the LLM; return text or tool_calls.

        Default (providers without tool support): forwards to
        `complete` and returns the text. Override in OpenAI / Codex /
        Anthropic for real function calling.
        """
        text = await self.complete(messages, **kwargs)
        return CompletionResult(text=text, tool_calls=[])
