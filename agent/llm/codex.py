"""OpenAI Codex CLI OAuth client.

Uses `~/.codex/auth.json` (managed by `codex login`) so the user does
not need a separate OPENAI_API_KEY. Calls the same endpoint the Codex
CLI itself uses:

    https://chatgpt.com/backend-api/codex/responses

Requirements (empirically discovered):
- Authorization: Bearer <access_token>
- chatgpt-account-id: <account_id>
- originator: codex_cli_rs
- Responses API payload shape (instructions + input list)
- stream: true (server rejects non-streaming)
- store: false (do not persist response server-side)

The SSE stream emits `response.output_text.delta` events whose payload
carries incremental text fragments; we accumulate and return the final
string. On 401 we re-read auth.json once (Codex CLI may have refreshed
the token in the background) and retry the call.
"""

from __future__ import annotations

import json
from typing import Any

from agent.llm.base import LLMClient


class _CodexUnauthorized(Exception):
    """Raised when the Codex endpoint returns 401, to trigger one refresh."""


class CodexClient(LLMClient):
    """ChatGPT-OAuth-authenticated client for the Codex responses endpoint."""

    ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"

    def __init__(
        self,
        model: str = "gpt-5.4",
        base_url: str | None = None,
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
                        f"Codex endpoint returned {resp.status_code}: "
                        f"{body[:300].decode(errors='replace')}"
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
