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
    """Client for OpenAI Codex CLI's ChatGPT OAuth endpoint.

    Uses `~/.codex/auth.json` (managed by `codex login`) so the user does
    not need a separate OPENAI_API_KEY. Calls the same endpoint the
    Codex CLI itself uses:

        https://chatgpt.com/backend-api/codex/responses

    Requirements (empirically discovered):
    - Authorization: Bearer <access_token>
    - chatgpt-account-id: <account_id>
    - originator: codex_cli_rs
    - Responses API payload shape (instructions + input list)
    - stream: true (server rejects non-streaming)
    - store: false (do not persist response server-side)

    The SSE stream emits `response.output_text.delta` events whose
    payload carries incremental text fragments; we accumulate and
    return the final string.

    On 401 we re-read auth.json once (Codex CLI may have refreshed
    the token in the background) and retry the call.
    """

    ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"

    def __init__(
        self,
        model: str = "gpt-5.4",
        base_url: str | None = None,  # kept for symmetry, usually unused
        temperature: float = 0.7,
        auth_path: str | None = None,
    ) -> None:
        from agent.codex_auth import read_codex_auth

        self.model = model
        self.temperature = temperature
        self.endpoint = base_url or self.ENDPOINT
        self._auth_path = auth_path
        auth = read_codex_auth(auth_path)
        self._token = auth.access_token
        self._account_id = auth.account_id or ""

    def _refresh_from_disk(self) -> None:
        from agent.codex_auth import read_codex_auth

        auth = read_codex_auth(self._auth_path)
        self._token = auth.access_token
        self._account_id = auth.account_id or ""

    def _build_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Convert OpenAI chat-format messages to Responses API format."""
        instructions_parts = []
        input_items: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                instructions_parts.append(content)
            else:
                # "user" or "assistant" both become input message items
                content_type = "input_text" if role == "user" else "output_text"
                input_items.append(
                    {
                        "type": "message",
                        "role": role,
                        "content": [{"type": content_type, "text": content}],
                    }
                )
        return {
            "model": self.model,
            "instructions": "\n\n".join(instructions_parts) or " ",
            "input": input_items,
            "stream": True,
            "store": False,
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "chatgpt-account-id": self._account_id,
            "originator": "codex_cli_rs",
        }

    @staticmethod
    def _accumulate_text_from_sse(raw: str) -> str:
        """Walk SSE events and concatenate response.output_text.delta pieces."""
        import json

        out: list[str] = []
        for block in raw.split("\n\n"):
            if "response.output_text.delta" not in block:
                continue
            for line in block.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                delta = payload.get("delta")
                if isinstance(delta, str):
                    out.append(delta)
        return "".join(out)

    async def _post(self, payload: dict[str, Any]) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", self.endpoint, headers=self._headers(), json=payload
            ) as resp:
                if resp.status_code == 401:
                    raise _CodexUnauthorized("401 Unauthorized")
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(
                        f"Codex endpoint returned {resp.status_code}: {body[:300].decode(errors='replace')}"
                    )
                chunks: list[str] = []
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)
        return self._accumulate_text_from_sse("".join(chunks))

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload = self._build_payload(messages)
        try:
            return await self._post(payload)
        except _CodexUnauthorized:
            self._refresh_from_disk()
            return await self._post(payload)


class _CodexUnauthorized(Exception):
    """Raised when the Codex endpoint returns 401, to trigger one refresh."""


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
