"""ToolRegistry — name→Tool mapping + executor."""

from __future__ import annotations

import inspect
import json
import traceback

from agent.tools.base import Tool, ToolCall, ToolResult


class ToolRegistry:
    """Registers tools and executes ToolCalls against them."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    async def execute(self, call: ToolCall) -> ToolResult:
        """Run a single ToolCall. Captures exceptions as error results."""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                content=f"Unknown tool: {call.name!r}",
                is_error=True,
            )
        try:
            result = tool.handler(**call.arguments)
            if inspect.isawaitable(result):
                result = await result
            content = result if isinstance(result, str) else json.dumps(result, default=str)
        except TypeError as e:
            return ToolResult(
                tool_call_id=call.id,
                content=f"Bad arguments for {call.name}: {e}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=call.id,
                content=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}",
                is_error=True,
            )
        return ToolResult(tool_call_id=call.id, content=content)
