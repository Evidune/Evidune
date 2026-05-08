"""Helpers for first-run and CLI-driven evidune.yaml edits."""

from __future__ import annotations

import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from core.config import load_config

ENV_REF_RE = re.compile(r"\$\{([^}]+)}")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def default_config_data(domain: str = "evidune") -> dict[str, Any]:
    """Return the smallest config that can be configured and served."""
    return {
        "domain": domain,
        "description": "",
        "agent": {},
        "gateways": [{"type": "cli"}],
        "channels": [{"type": "stdout"}],
    }


def load_raw_config(path: str | Path, *, create: bool = False) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        if not create:
            raise FileNotFoundError(f"Config file not found: {path}")
        return default_config_data(path.parent.resolve().name or "evidune")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(data).__name__}")
    return data


def write_raw_config(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(rendered)
    temp_path.replace(path)


def env_ref(env_name: str | None) -> str | None:
    if env_name is None:
        return None
    env_name = env_name.strip()
    if not ENV_NAME_RE.match(env_name):
        raise ValueError(f"Invalid environment variable name: {env_name}")
    return f"${{{env_name}}}"


def missing_env_vars(data: Any) -> list[str]:
    missing: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            for name in ENV_REF_RE.findall(value):
                if os.environ.get(name) is None:
                    missing.add(name)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return sorted(missing)


def missing_config_env_vars(data: dict[str, Any]) -> list[str]:
    """Return missing env vars from ${VAR} references and config env-name fields."""
    missing = set(missing_env_vars(data))
    agent = data.get("agent")
    if isinstance(agent, dict):
        for key in ("api_key_env",):
            name = agent.get(key)
            if isinstance(name, str) and name and os.environ.get(name) is None:
                missing.add(name)
        evaluator = agent.get("evaluator")
        if isinstance(evaluator, dict):
            name = evaluator.get("api_key_env")
            if isinstance(name, str) and name and os.environ.get(name) is None:
                missing.add(name)
    return sorted(missing)


@contextmanager
def _placeholder_env(names: Iterable[str]):
    old: dict[str, str | None] = {}
    for name in names:
        old[name] = os.environ.get(name)
        if old[name] is None:
            os.environ[name] = "__EVIDUNE_CONFIG_PLACEHOLDER__"
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def validate_config_structure(path: str | Path) -> list[str]:
    """Validate config shape while preserving missing-secret diagnostics."""
    data = load_raw_config(path)
    missing = missing_env_vars(data)
    with _placeholder_env(missing):
        load_config(path)
    return missing


def ensure_config_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    data = load_raw_config(path, create=True)
    if not path.exists():
        write_raw_config(path, data)
    return data


def configure_model(
    data: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    evaluator_provider: str | None = None,
    evaluator_model: str | None = None,
    evaluator_base_url: str | None = None,
    evaluator_api_key_env: str | None = None,
) -> None:
    agent = data.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("agent section must be a mapping")
    if provider:
        agent["llm_provider"] = provider
    if model:
        agent["llm_model"] = model
    if base_url is not None:
        agent["llm_base_url"] = base_url or None
    if api_key_env:
        agent["api_key_env"] = api_key_env.strip()

    evaluator_values = {
        "llm_provider": evaluator_provider,
        "llm_model": evaluator_model,
        "llm_base_url": evaluator_base_url,
        "api_key_env": evaluator_api_key_env,
    }
    if any(value is not None and value != "" for value in evaluator_values.values()):
        evaluator = agent.setdefault("evaluator", {})
        if not isinstance(evaluator, dict):
            raise ValueError("agent.evaluator section must be a mapping")
        for key, value in evaluator_values.items():
            if value is not None:
                evaluator[key] = value or None


def normalize_gateway_type(kind: str | None) -> str:
    if not kind:
        raise ValueError("gateway type is required")
    normalized = kind.strip().lower()
    if normalized == "feishu":
        return "feishu_bot"
    if normalized not in {"cli", "web", "feishu_bot"}:
        raise ValueError("gateway type must be one of: cli, web, feishu_bot")
    return normalized


def _gateways(data: dict[str, Any]) -> list[dict[str, Any]]:
    gateways = data.setdefault("gateways", [])
    if not isinstance(gateways, list):
        raise ValueError("gateways section must be a list")
    return gateways


def upsert_gateway(data: dict[str, Any], gateway: dict[str, Any]) -> None:
    gateway_type = normalize_gateway_type(gateway.get("type"))
    gateway["type"] = gateway_type
    gateways = _gateways(data)
    for index, existing in enumerate(gateways):
        if (
            isinstance(existing, dict)
            and normalize_gateway_type(existing.get("type")) == gateway_type
        ):
            gateways[index] = gateway
            return
    gateways.append(gateway)


def remove_gateway(data: dict[str, Any], kind: str) -> int:
    gateway_type = normalize_gateway_type(kind)
    gateways = _gateways(data)
    before = len(gateways)
    data["gateways"] = [
        gw
        for gw in gateways
        if not (isinstance(gw, dict) and normalize_gateway_type(gw.get("type")) == gateway_type)
    ]
    return before - len(data["gateways"])


def configured_gateways(data: dict[str, Any]) -> list[dict[str, Any]]:
    gateways = _gateways(data)
    return [gw for gw in gateways if isinstance(gw, dict)]


def gateway_from_options(
    kind: str,
    *,
    host: str | None = None,
    port: int | None = None,
    app_id_env: str | None = None,
    app_secret_env: str | None = None,
    domain: str | None = None,
    reply_mode: str | None = None,
    allowed_open_ids: list[str] | None = None,
    allowed_chat_ids: list[str] | None = None,
) -> dict[str, Any]:
    gateway_type = normalize_gateway_type(kind)
    if gateway_type == "cli":
        return {"type": "cli"}
    if gateway_type == "web":
        return {
            "type": "web",
            "host": host or "127.0.0.1",
            "port": int(port or 8080),
        }
    gateway: dict[str, Any] = {
        "type": "feishu_bot",
        "app_id": env_ref(app_id_env) if app_id_env else "",
        "app_secret": env_ref(app_secret_env) if app_secret_env else "",
        "domain": domain or "https://open.feishu.cn",
        "reply_mode": reply_mode or "card",
    }
    if allowed_open_ids:
        gateway["allowed_open_ids"] = allowed_open_ids
    if allowed_chat_ids:
        gateway["allowed_chat_ids"] = allowed_chat_ids
    return gateway


def test_gateway(gateway: dict[str, Any]) -> tuple[bool, str]:
    gateway_type = normalize_gateway_type(gateway.get("type"))
    missing = missing_env_vars(gateway)
    if missing:
        return False, f"{gateway_type}: missing environment variables: {', '.join(missing)}"
    if gateway_type == "cli":
        return True, "cli: configured"
    if gateway_type == "feishu_bot":
        required = [key for key in ("app_id", "app_secret", "domain") if not gateway.get(key)]
        if required:
            return False, f"feishu_bot: missing required fields: {', '.join(required)}"
        return True, "feishu_bot: configured (live credential test skipped)"

    host = gateway.get("host") or "127.0.0.1"
    port = int(gateway.get("port") or 8080)
    url = f"http://{host}:{port}/api/skills"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if 200 <= response.status < 300:
                return True, f"web: reachable at {url}"
            return False, f"web: {url} returned HTTP {response.status}"
    except (OSError, urllib.error.URLError) as exc:
        return False, f"web: not reachable at {url}: {exc}"
