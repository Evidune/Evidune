"""One-click Feishu app registration and local credential storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from gateway.feishu_support import load_lark

APP_ID_ENV = "FEISHU_APP_ID"
APP_SECRET_ENV = "FEISHU_APP_SECRET"
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def local_credentials_path(config_path: str | Path) -> Path:
    return Path(config_path).resolve().parent / ".evidune" / "credentials.json"


def load_local_credentials(config_path: str | Path) -> Path | None:
    """Load project-local credentials without overriding explicit process env."""
    path = local_credentials_path(config_path)
    if not path.exists():
        return None
    credentials = _read_credentials(path)
    for name, value in credentials.items():
        os.environ.setdefault(name, value)
    path.chmod(0o600)
    return path


def save_local_credentials(config_path: str | Path, credentials: dict[str, str]) -> Path:
    """Merge credentials into the ignored project-local store and export them."""
    path = local_credentials_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_credentials(path) if path.exists() else {}
    for name, value in credentials.items():
        if not _ENV_NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid environment variable name: {name}")
        if not isinstance(value, str) or not value:
            raise ValueError(f"Credential {name} must be a non-empty string")
    current.update(credentials)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(current, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    path.chmod(0o600)
    os.environ.update(credentials)
    return path


def register_feishu_app(
    *,
    domain_name: str,
    open_browser: bool = True,
) -> dict[str, str]:
    """Create a PersonalAgent app through Feishu's official device flow."""
    try:
        lark = load_lark()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    register_app = getattr(lark, "register_app", None)
    if not callable(register_app):
        raise ValueError(
            "One-click Feishu setup requires lark-oapi>=1.7.1,<2. "
            'Install it with `pip install -e ".[feishu]"`.'
        )

    def on_qr_code(info: dict[str, Any]) -> None:
        url = str(info.get("url") or "")
        if not url:
            raise ValueError("Feishu registration did not return a verification URL")
        expires = int(info.get("expire_in") or 600)
        print("Open this link in Feishu or Lark to create the Evidune bot:")
        print(url)
        print(f"The link expires in {expires} seconds.")
        if open_browser:
            try:
                webbrowser.open(url)
            except OSError:
                print("Could not open a browser automatically; use the link above.")

    try:
        result = register_app(
            on_qr_code=on_qr_code,
            source="evidune",
            app_preset={
                "name": f"Evidune · {domain_name}",
                "desc": "Evidune outcome-driven AI agent",
            },
            create_only=True,
        )
    except ValueError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        description = getattr(exc, "description", str(exc))
        raise ValueError(f"Feishu app registration failed ({code}): {description}") from exc

    app_id = result.get("client_id") if isinstance(result, dict) else None
    app_secret = result.get("client_secret") if isinstance(result, dict) else None
    if not isinstance(app_id, str) or not app_id:
        raise ValueError("Feishu app registration did not return an App ID")
    if not isinstance(app_secret, str) or not app_secret:
        raise ValueError("Feishu app registration did not return an App Secret")

    user_info = result.get("user_info") if isinstance(result, dict) else {}
    user_info = user_info if isinstance(user_info, dict) else {}
    tenant_brand = str(user_info.get("tenant_brand") or "feishu").lower()
    api_domain = (
        "https://open.larksuite.com" if tenant_brand == "lark" else "https://open.feishu.cn"
    )
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "api_domain": api_domain,
    }


def _read_credentials(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid local credential store: {path}") from exc
    if not isinstance(data, dict) or any(
        not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name) or not isinstance(value, str)
        for name, value in data.items()
    ):
        raise ValueError(f"Local credential store must be a string mapping: {path}")
    return data
