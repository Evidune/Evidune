"""Tests for agent/codex_auth.py."""

import json
import stat
import urllib.parse
from pathlib import Path

import pytest

from agent.codex_auth import (
    CODEX_CLIENT_ID,
    CodexAuth,
    CodexAuthError,
    get_access_token,
    read_codex_auth,
    refresh_codex_auth,
)


def _write_auth(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return path


VALID_AUTH = {
    "auth_mode": "chatgpt",
    "tokens": {
        "access_token": "sk-codex-access-token-abc",
        "id_token": "id-token-xyz",
        "refresh_token": "refresh-token-456",
        "account_id": "acct-uuid-aaaa",
    },
    "last_refresh": "2026-04-14T10:00:00Z",
}


class TestReadCodexAuth:
    def test_reads_valid_file(self, tmp_path: Path):
        path = _write_auth(tmp_path / "auth.json", VALID_AUTH)
        auth = read_codex_auth(path)
        assert isinstance(auth, CodexAuth)
        assert auth.access_token == "sk-codex-access-token-abc"
        assert auth.refresh_token == "refresh-token-456"
        assert auth.id_token == "id-token-xyz"
        assert auth.account_id == "acct-uuid-aaaa"
        assert auth.auth_mode == "chatgpt"
        assert auth.last_refresh == "2026-04-14T10:00:00Z"
        assert auth.source_path == path

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(CodexAuthError, match="not found"):
            read_codex_auth(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(CodexAuthError, match="not valid JSON"):
            read_codex_auth(path)

    def test_missing_access_token_raises(self, tmp_path: Path):
        path = _write_auth(tmp_path / "auth.json", {"auth_mode": "chatgpt", "tokens": {}})
        with pytest.raises(CodexAuthError, match="no tokens.access_token"):
            read_codex_auth(path)

    def test_missing_tokens_section_raises(self, tmp_path: Path):
        path = _write_auth(tmp_path / "auth.json", {"auth_mode": "chatgpt"})
        with pytest.raises(CodexAuthError, match="no tokens.access_token"):
            read_codex_auth(path)

    def test_uses_default_path_when_omitted(self, tmp_path: Path, monkeypatch):
        # Point HOME to tmp_path so the default ~/.codex/auth.json resolves there
        monkeypatch.setattr("agent.codex_auth.DEFAULT_AUTH_PATH", tmp_path / ".codex" / "auth.json")
        _write_auth(tmp_path / ".codex" / "auth.json", VALID_AUTH)
        auth = read_codex_auth()
        assert auth.access_token == "sk-codex-access-token-abc"

    def test_optional_fields_default(self, tmp_path: Path):
        minimal = {"tokens": {"access_token": "tok"}}
        path = _write_auth(tmp_path / "auth.json", minimal)
        auth = read_codex_auth(path)
        assert auth.access_token == "tok"
        assert auth.refresh_token is None
        assert auth.id_token is None
        assert auth.auth_mode == "chatgpt"  # default
        assert auth.last_refresh is None


class TestGetAccessToken:
    def test_returns_token_string(self, tmp_path: Path):
        path = _write_auth(tmp_path / "auth.json", VALID_AUTH)
        token = get_access_token(path)
        assert token == "sk-codex-access-token-abc"

    def test_propagates_error(self, tmp_path: Path):
        with pytest.raises(CodexAuthError):
            get_access_token(tmp_path / "nope.json")


class TestRefreshCodexAuth:
    def test_refresh_updates_auth_file_atomically(self, tmp_path: Path, monkeypatch):
        path = _write_auth(
            tmp_path / "auth.json",
            {
                **VALID_AUTH,
                "OPENAI_API_KEY": "sk-codex-access-token-abc",
            },
        )
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "access_token": "sk-new-access",
                        "refresh_token": "new-refresh",
                        "id_token": "new-id",
                        "chatgpt_account_id": "acct-new",
                    }
                ).encode()

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["content_type"] = request.get_header("Content-type")
            captured["form"] = urllib.parse.parse_qs(request.data.decode())
            return FakeResponse()

        monkeypatch.setattr("agent.codex_auth.urllib.request.urlopen", fake_urlopen)

        auth = refresh_codex_auth(path, token_url="https://chatgpt.test/oauth/token", timeout_s=9)

        assert captured["url"] == "https://chatgpt.test/oauth/token"
        assert captured["timeout"] == 9
        assert captured["content_type"] == "application/x-www-form-urlencoded"
        assert captured["form"] == {
            "grant_type": ["refresh_token"],
            "refresh_token": ["refresh-token-456"],
            "client_id": [CODEX_CLIENT_ID],
        }
        assert auth.access_token == "sk-new-access"
        assert auth.refresh_token == "new-refresh"
        assert auth.id_token == "new-id"
        assert auth.account_id == "acct-new"

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["OPENAI_API_KEY"] == "sk-new-access"
        assert raw["tokens"]["access_token"] == "sk-new-access"
        assert raw["tokens"]["refresh_token"] == "new-refresh"
        assert raw["tokens"]["id_token"] == "new-id"
        assert raw["tokens"]["account_id"] == "acct-new"
        assert raw["last_refresh"].endswith("Z")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_missing_refresh_token_raises(self, tmp_path: Path):
        path = _write_auth(tmp_path / "auth.json", {"tokens": {"access_token": "sk-old"}})

        with pytest.raises(CodexAuthError, match="tokens.refresh_token"):
            refresh_codex_auth(path)
