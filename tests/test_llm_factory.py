"""Tests for the create_llm_client factory and CodexClient."""

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.llm import AnthropicClient, CodexClient, LocalClient, OpenAIClient, create_llm_client


def _write_codex_auth(path: Path, token: str = "sk-tok-x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": token}}),
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

    @pytest.mark.asyncio
    async def test_complete_delegates_to_inner(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))

        # Replace inner with a mock
        mock_inner = MagicMock()

        async def fake_complete(messages, **kwargs):
            return "hello from inner"

        mock_inner.complete = fake_complete
        client._inner = mock_inner

        result = await client.complete([{"role": "user", "content": "hi"}])
        assert result == "hello from inner"

    @pytest.mark.asyncio
    async def test_401_triggers_token_refresh(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json", "sk-old-token")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))

        # First call raises 401, then we re-write the auth file, then retry succeeds
        call_count = {"n": 0}

        async def flaky_complete(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("HTTP 401: Unauthorized")
            return "second-call-success"

        # Patch the build_inner to keep returning a mock that uses flaky_complete
        with patch.object(client, "_build_inner") as mock_build:
            mock_inner = MagicMock()
            mock_inner.complete = flaky_complete
            mock_build.return_value = mock_inner
            client._inner = mock_inner

            # Rotate the token on disk before retry
            _write_codex_auth(auth_path, "sk-rotated-token")

            result = await client.complete([{"role": "user", "content": "hi"}])
            assert result == "second-call-success"
            assert client._token == "sk-rotated-token"
            assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_non_auth_error_propagates(self, tmp_path: Path):
        auth_path = _write_codex_auth(tmp_path / "auth.json")
        client = CodexClient(model="gpt-5.4", auth_path=str(auth_path))

        async def boom(messages, **kwargs):
            raise RuntimeError("rate limited")

        client._inner.complete = boom

        with pytest.raises(RuntimeError, match="rate limited"):
            await client.complete([{"role": "user", "content": "hi"}])
