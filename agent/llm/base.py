"""LLMClient abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Base class for LLM providers.

    All clients are async and return the final response text as a string.
    Streaming is handled internally per provider (some providers require it).
    """

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send messages to the LLM and return the response text."""
        ...
