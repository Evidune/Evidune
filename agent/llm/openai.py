"""OpenAI-compatible client."""

from __future__ import annotations

import json
from typing import Any

from agent.llm.base import LLMClient
from agent.tools.base import CompletionResult, Tool, ToolCall


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
            raise ImportError("Install openai: pip install evidune[openai]") from e

        self.model = model
        self.temperature = temperature
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore
            temperature=kwargs.get("temperature", self.temperature),
        )
        return resp.choices[0].message.content or ""

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        **kwargs: Any,
    ) -> CompletionResult:
        tool_schema = [_tool_to_openai_schema(t) for t in tools] if tools else None
        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if tool_schema:
            call_kwargs["tools"] = tool_schema
        resp = await self._client.chat.completions.create(**call_kwargs)
        msg = resp.choices[0].message

        text = msg.content or ""
        tool_calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return CompletionResult(text=text, tool_calls=tool_calls)


def _tool_to_openai_schema(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
