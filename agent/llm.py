"""LLM client abstraction — supports OpenAI, Anthropic, and local endpoints."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send messages to the LLM and return the response text."""
        ...


class OpenAIClient(LLMClient):
    """OpenAI-compatible client (also works with local servers like Ollama, vLLM)."""

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


class LocalClient(LLMClient):
    """Local HTTP endpoint client (Ollama, vLLM, LMStudio, etc.)."""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434/v1",
        temperature: float = 0.7,
    ) -> None:
        # Reuse OpenAI client with custom base_url
        self._inner = OpenAIClient(
            model=model,
            api_key="not-needed",
            base_url=base_url,
            temperature=temperature,
        )

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return await self._inner.complete(messages, **kwargs)


class CodexClient(LLMClient):
    """OpenAI-compatible client authed via the Codex CLI's stored token.

    Reuses `~/.codex/auth.json` (managed by `codex login`) so the user
    does not need a separate OPENAI_API_KEY in env. On a 401 we re-read
    the auth file once (Codex CLI may have refreshed the token in the
    background) and retry the call.
    """

    def __init__(
        self,
        model: str = "gpt-5.4",
        base_url: str | None = None,
        temperature: float = 0.7,
        auth_path: str | None = None,
    ) -> None:
        from agent.codex_auth import get_access_token

        self.model = model
        self.temperature = temperature
        self.base_url = base_url
        self._auth_path = auth_path
        self._token = get_access_token(auth_path)
        self._inner = self._build_inner()

    def _build_inner(self) -> OpenAIClient:
        return OpenAIClient(
            model=self.model,
            api_key=self._token,
            base_url=self.base_url,
            temperature=self.temperature,
        )

    def _refresh_from_disk(self) -> None:
        from agent.codex_auth import get_access_token

        self._token = get_access_token(self._auth_path)
        self._inner = self._build_inner()

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            return await self._inner.complete(messages, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "401" in msg or "unauthorized" in msg or "invalid_api_key" in msg:
                self._refresh_from_disk()
                return await self._inner.complete(messages, **kwargs)
            raise


def create_llm_client(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> LLMClient:
    """Factory function to create an LLM client."""
    if provider == "openai":
        return OpenAIClient(
            model=model, api_key=api_key, base_url=base_url, temperature=temperature
        )
    elif provider == "anthropic":
        return AnthropicClient(model=model, api_key=api_key, temperature=temperature, **kwargs)
    elif provider == "local":
        return LocalClient(
            model=model, base_url=base_url or "http://localhost:11434/v1", temperature=temperature
        )
    elif provider == "codex":
        return CodexClient(
            model=model,
            base_url=base_url,
            temperature=temperature,
            auth_path=kwargs.get("auth_path"),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
