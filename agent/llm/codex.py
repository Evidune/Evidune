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

The SSE stream emits `response.output_text.delta` events for text and
`response.output_item.done` events (with item.type == "function_call")
for tool calls. On 401 we recover auth once: first by re-reading
auth.json in case another process refreshed it, then by using the
stored refresh_token to update auth.json, and finally retrying the call.
"""

from __future__ import annotations

import json
from typing import Any

from agent.llm.base import LLMClient
from agent.tools.base import CompletionResult, Tool, ToolCall


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

    def _reload_from_disk(self) -> None:
        from agent.codex_auth import read_codex_auth

        auth = read_codex_auth(self._auth_path)
        self._token = auth.access_token
        self._account_id = auth.account_id or ""

    def _recover_after_unauthorized(self) -> None:
        from agent.codex_auth import read_codex_auth, refresh_codex_auth

        auth = read_codex_auth(self._auth_path)
        if auth.access_token != self._token:
            self._token = auth.access_token
            self._account_id = auth.account_id or ""
            return

        auth = refresh_codex_auth(self._auth_path)
        self._token = auth.access_token
        self._account_id = auth.account_id or ""

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
    ) -> dict[str, Any]:
        """Convert OpenAI chat-format messages + tools to Responses API format."""
        instructions_parts: list[str] = []
        input_items: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                instructions_parts.append(m.get("content", ""))
                continue
            if role == "tool":
                # Tool result from a previous turn
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.get("tool_call_id", ""),
                        "output": m.get("content", ""),
                    }
                )
                continue
            if role == "assistant" and (m.get("tool_calls") or m.get("_evidune_tool_calls")):
                # Assistant's tool-call turn — prefer the evidune-native
                # representation (already parsed args) over OpenAI's
                # string-encoded one.
                native = m.get("_evidune_tool_calls")
                if native:
                    for tc in native:
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": tc["id"],
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments", {})),
                            }
                        )
                else:
                    for tc in m["tool_calls"]:
                        fn = tc.get("function", {})
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": tc.get("id", ""),
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", "{}"),
                            }
                        )
                if m.get("content"):
                    input_items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": m["content"]}],
                        }
                    )
                continue

            content = m.get("content", "")
            content_type = "input_text" if role == "user" else "output_text"
            input_items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": content_type, "text": content}],
                }
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": "\n\n".join(instructions_parts) or " ",
            "input": input_items,
            "stream": True,
            "store": False,
        }
        if tools:
            payload["tools"] = [_tool_to_codex_schema(t) for t in tools]
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "chatgpt-account-id": self._account_id,
            "originator": "codex_cli_rs",
        }

    @staticmethod
    def _parse_sse(raw: str) -> tuple[str, list[ToolCall]]:
        """Walk SSE events. Returns (accumulated_text, tool_calls)."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in raw.split("\n\n"):
            if not block.strip():
                continue
            for line in block.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                etype = payload.get("type", "")
                if etype == "response.output_text.delta":
                    delta = payload.get("delta")
                    if isinstance(delta, str):
                        text_parts.append(delta)
                elif etype == "response.output_item.done":
                    item = payload.get("item") or {}
                    if item.get("type") == "function_call":
                        try:
                            args = json.loads(item.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append(
                            ToolCall(
                                id=item.get("call_id") or item.get("id") or "",
                                name=item.get("name") or "",
                                arguments=args,
                            )
                        )
        return "".join(text_parts), tool_calls

    async def _post(self, payload: dict[str, Any]) -> tuple[str, list[ToolCall]]:
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
        return self._parse_sse("".join(chunks))

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        payload = self._build_payload(messages)
        try:
            text, _ = await self._post(payload)
        except _CodexUnauthorized:
            self._recover_after_unauthorized()
            text, _ = await self._post(payload)
        return text

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        **kwargs: Any,
    ) -> CompletionResult:
        payload = self._build_payload(messages, tools=tools)
        try:
            text, tool_calls = await self._post(payload)
        except _CodexUnauthorized:
            self._recover_after_unauthorized()
            text, tool_calls = await self._post(payload)
        return CompletionResult(text=text, tool_calls=tool_calls)

    # Backward-compat alias: old tests exercised _accumulate_text_from_sse
    @staticmethod
    def _accumulate_text_from_sse(raw: str) -> str:
        text, _ = CodexClient._parse_sse(raw)
        return text


def _tool_to_codex_schema(tool: Tool) -> dict[str, Any]:
    """Responses API tool schema (flat, not nested under 'function')."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
