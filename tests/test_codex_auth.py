"""Tests for agent/codex_auth.py."""

import json
import stat
from pathlib import Path

import pytest

from agent.codex_auth import CodexAuth, CodexAuthError, get_access_token, read_codex_auth


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
