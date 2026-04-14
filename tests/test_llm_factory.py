"""Tests for the create_llm_client factory and CodexClient."""

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.llm import AnthropicClient, CodexClient, LocalClient, OpenAIClient, create_llm_client


def _write_codex_auth(path: Path, token: str = "sk-tok-x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": token,
                    "account_id": "test-acct-id",
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


class TestFactory:
    @pytest.mark.skipif(not _has_module("openai"), reason="openai SDK not installed")
    def test_openai_provider(self):
        client = create_llm_client("openai", "gpt-4o", api_key="sk-test")
        assert isinstance(client, OpenAIClient)

    @pytest.mark.skipif(not _has_module("anthropic"), reason="anthropic SDK not installed")
    def test_anthropic_provider(self):
        client = create_llm_client("anthropic", "claude-sonnet-4-6", api_key="sk-test")
        assert isinstance(client, AnthropicClient)

    @pytest.mark.skipif(not _has_module("openai"), reason="openai SDK not installed")
    def test_local_provider(self):
        client = create_llm_client("local", "llama3", base_url="http://localhost:11434/v1")
        assert isinstance(client, LocalClient)

    @pytest.mark.skipif(not _has_module("openai"), reason="openai SDK not installed")
    def test_codex_provider(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = create_llm_client("codex", "gpt-5.4", auth_path=str(auth_path))
        assert isinstance(client, CodexClient)
        assert client._token == "sk-tok-x"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_client("xyz", "model")


@pytest.mark.skipif(not _has_module("openai"), reason="openai SDK not installed")
class TestCodexClient:
    def test_loads_token_at_init(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json", "sk-init-token")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))
        assert client._token == "sk-init-token"

    def test_missing_auth_raises(self, tmp_path: Path):
        from agent.codex_auth import CodexAuthError

        with pytest.raises(CodexAuthError):
            CodexClient(model="gpt-5.4", auth_path=str(tmp_path / "nope.json"))

    def test_builds_payload_separates_system(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))
        payload = client._build_payload(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Ok"},
            ]
        )
        assert payload["instructions"] == "You are helpful."
        assert payload["stream"] is True
        assert payload["store"] is False
        assert payload["model"] == "gpt-5.4"
        # System becomes instructions; remaining turns become input items
        assert len(payload["input"]) == 3
        assert payload["input"][0]["role"] == "user"
        assert payload["input"][1]["role"] == "assistant"

    def test_headers_include_required_fields(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json", "sk-tok-x")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))
        headers = client._headers()
        assert headers["Authorization"] == "Bearer sk-tok-x"
        assert headers["originator"] == "codex_cli_rs"
        assert "chatgpt-account-id" in headers

    def test_accumulate_sse_deltas(self):
        raw = (
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta","delta":" world"}\n\n'
            "event: response.completed\n"
            'data: {"type":"response.completed"}\n\n'
        )
        assert CodexClient._accumulate_text_from_sse(raw) == "Hello world"

    def test_accumulate_ignores_unknown_events(self):
        raw = (
            "event: response.created\n"
            'data: {"type":"response.created"}\n\n'
            "event: response.output_text.delta\n"
            'data: {"type":"response.output_text.delta","delta":"A"}\n\n'
        )
        assert CodexClient._accumulate_text_from_sse(raw) == "A"

    @pytest.mark.asyncio
    async def test_complete_uses_post(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))

        async def fake_post(payload):
            assert payload["model"] == "gpt-5.4"
            assert payload["stream"] is True
            return "response text"

        with patch.object(client, "_post", side_effect=fake_post):
            result = await client.complete([{"role": "user", "content": "hi"}])
        assert result == "response text"

    @pytest.mark.asyncio
    async def test_401_triggers_token_refresh(self, tmp_path: Path):
        from agent.llm import _CodexUnauthorized

        auth_path = _write_codex_auth(tmp_path / "auth.json", "sk-old")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))
        call_count = {"n": 0}

        async def flaky_post(payload):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _CodexUnauthorized("401")
            return "recovered"

        with patch.object(client, "_post", side_effect=flaky_post):
            # Rotate the token on disk so the refresh picks up the new one
            _write_codex_auth(auth_path, "sk-new")
            result = await client.complete([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        assert client._token == "sk-new"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_non_auth_error_propagates(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))

        async def boom(payload):
            raise RuntimeError("rate limited")

        with patch.object(client, "_post", side_effect=boom):
            with pytest.raises(RuntimeError, match="rate limited"):
                await client.complete([{"role": "user", "content": "hi"}])
