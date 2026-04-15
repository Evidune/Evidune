"""Tool abstraction for agent tool-use loops.

A Tool is a named, schema-typed, async callable that the LLM can invoke
via OpenAI-compatible function calling. The agent hands a list of Tools
to the LLM; when the LLM responds with a ToolCall, the agent executes
the tool and feeds the ToolResult back into the next LLM turn.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tool:
    """A single tool the LLM may call.

    `parameters` is a JSON-Schema dict describing the tool's arguments,
    as required by OpenAI's function-calling spec.

    `handler` is an async callable receiving the parsed kwargs and
    returning any JSON-serialisable value (or a string). If the handler
    raises, the agent records the error as a ToolResult with is_error=True.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]


@dataclass
class ToolCall:
    """One tool invocation requested by the LLM."""

    id: str  # provider-generated call id (for matching results back)
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Outcome of executing a ToolCall."""

    tool_call_id: str
    content: str  # stringified result or error message
    is_error: bool = False


@dataclass
class CompletionResult:
    """What the LLM returned this turn.

    Either `text` is non-empty (final response) or `tool_calls` is
    non-empty (agent should execute them and re-invoke the LLM), or
    both (some providers emit partial text alongside tool calls).
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls
