"""Codex CLI auth integration.

Reads `~/.codex/auth.json` (managed by OpenAI Codex CLI) and extracts
the OAuth access_token. This lets aiflay reuse the user's existing
ChatGPT subscription auth without requiring a separate OPENAI_API_KEY.

We intentionally do NOT refresh tokens ourselves — the Codex CLI is
the source of truth and refreshes on its own schedule. We just re-read
the file when the cached token stops working.

The auth.json shape (as of 2026):
    {
      "auth_mode": "chatgpt",
      "tokens": {
        "access_token": "sk-...",
        "id_token": "...",
        "refresh_token": "..."
      },
      "last_refresh": "2026-04-14T10:00:00Z"
    }

Treat this file like a password: never log, copy, or commit it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AUTH_PATH = Path.home() / ".codex" / "auth.json"


class CodexAuthError(RuntimeError):
    """Raised when the Codex auth file cannot be read or is invalid."""


@dataclass
class CodexAuth:
    """Parsed Codex auth payload."""

    access_token: str
    auth_mode: str = "chatgpt"
    refresh_token: str | None = None
    id_token: str | None = None
    last_refresh: str | None = None
    source_path: Path = DEFAULT_AUTH_PATH


def read_codex_auth(path: str | Path | None = None) -> CodexAuth:
    """Read and parse the Codex auth.json file.

    Args:
        path: Optional override for the auth file location.
              Defaults to `~/.codex/auth.json`.

    Returns:
        CodexAuth with the access_token populated.

    Raises:
        CodexAuthError: if the file is missing, unreadable, or malformed.
    """
    auth_path = Path(path) if path else DEFAULT_AUTH_PATH

    if not auth_path.is_file():
        raise CodexAuthError(
            f"Codex auth file not found at {auth_path}. "
            "Run `codex login` first, or set a different path."
        )

    try:
        raw = auth_path.read_text(encoding="utf-8")
    except OSError as e:
        raise CodexAuthError(f"Cannot read {auth_path}: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CodexAuthError(f"{auth_path} is not valid JSON: {e}") from e

    tokens = data.get("tokens") or {}
    access_token = tokens.get("access_token")
    if not access_token:
        raise CodexAuthError(
            f"{auth_path} has no tokens.access_token. "
            "The file format may have changed; try `codex login` to refresh it."
        )

    return CodexAuth(
        access_token=access_token,
        auth_mode=data.get("auth_mode", "chatgpt"),
        refresh_token=tokens.get("refresh_token"),
        id_token=tokens.get("id_token"),
        last_refresh=data.get("last_refresh"),
        source_path=auth_path,
    )


def get_access_token(path: str | Path | None = None) -> str:
    """Convenience: read auth and return only the access_token.

    Raises CodexAuthError on any problem.
    """
    return read_codex_auth(path).access_token
