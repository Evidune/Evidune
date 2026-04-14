"""Shared test fixtures and helpers."""

from __future__ import annotations

from agent.llm import LLMClient


class MockJudge(LLMClient):
    """Configurable LLM stub used by evaluator / extractor / detector /
    synthesiser tests.

    The test passes the response text to return; the mock captures the
    last `messages` list and `**kwargs` for assertions.
    """

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.last_messages: list[dict[str, str]] | None = None
        self.last_kwargs: dict = {}

    async def complete(self, messages, **kwargs):  # noqa: D401 — keep interface
        self.last_messages = messages
        self.last_kwargs = kwargs
        return self.response
