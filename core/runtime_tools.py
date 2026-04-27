"""Structured runtime self-management tools for the serve agent."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from agent.tools.base import Tool
from core.config import EviduneConfig, load_config


def _load_raw_config(config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")
    return raw


def _path_segments(path: str) -> list[str]:
    cleaned = path.strip()
    if not cleaned:
        return []
    segments = cleaned.split(".")
    if any(not segment for segment in segments):
        raise ValueError(f"Invalid config path {path!r}")
    return segments


def _descend(value: Any, segment: str) -> Any:
    if isinstance(value, dict):
        if segment not in value:
            raise KeyError(segment)
        return value[segment]
    if isinstance(value, list):
        if not segment.isdigit():
            raise ValueError(f"List path segment must be numeric, got {segment!r}")
        index = int(segment)
        try:
            return value[index]
        except IndexError as exc:
            raise KeyError(segment) from exc
    raise ValueError(f"Cannot descend into {type(value).__name__} at {segment!r}")


def _get_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for segment in _path_segments(path):
        value = _descend(value, segment)
    return value


def _ensure_parent(data: dict[str, Any], path: str) -> tuple[Any, str]:
    segments = _path_segments(path)
    if not segments:
        raise ValueError("Patch path cannot be empty")
    parent: Any = data
    for segment in segments[:-1]:
        if isinstance(parent, dict):
            if segment not in parent or parent[segment] is None:
                parent[segment] = {}
            parent = parent[segment]
        elif isinstance(parent, list):
            if not segment.isdigit():
                raise ValueError(f"List path segment must be numeric, got {segment!r}")
            parent = parent[int(segment)]
        else:
            raise ValueError(f"Cannot descend into {type(parent).__name__} at {segment!r}")
    return parent, segments[-1]


def _set_value(data: dict[str, Any], path: str, value: Any) -> Any:
    parent, leaf = _ensure_parent(data, path)
    if isinstance(parent, dict):
        before = parent.get(leaf)
        parent[leaf] = value
        return before
    if isinstance(parent, list):
        if not leaf.isdigit():
            raise ValueError(f"List path segment must be numeric, got {leaf!r}")
        index = int(leaf)
        before = parent[index]
        parent[index] = value
        return before
    raise ValueError(f"Cannot set {leaf!r} on {type(parent).__name__}")


def _dump_config(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _timestamp() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime())


def _effective_config(path: Path) -> dict[str, Any]:
    return asdict(load_config(path))


def _agent_runtime_summary(config: EviduneConfig) -> dict[str, Any]:
    if config.agent is None:
        return {"agent_configured": False}
    return {
        "agent_configured": True,
        "llm_provider": config.agent.llm_provider,
        "llm_model": config.agent.llm_model,
        "llm_base_url": config.agent.llm_base_url,
        "api_key_env": config.agent.api_key_env,
    }


def _load_agent_runtime_summary(path: Path) -> dict[str, Any]:
    return _agent_runtime_summary(load_config(path))


def _safe_agent_runtime_summary(path: Path) -> dict[str, Any]:
    try:
        return _load_agent_runtime_summary(path)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _validate_and_summarise_config_data(config_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".yaml", dir=str(config_path.parent), delete=False
    ) as handle:
        handle.write(_dump_config(data))
        temp_path = Path(handle.name)
    try:
        return _load_agent_runtime_summary(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def runtime_tools(config_path: Path, base_dir: Path) -> list[Tool]:
    """Build self-management tools scoped to the configured project."""

    resolved_config_path = Path(config_path).resolve()
    resolved_base_dir = Path(base_dir).resolve()

    async def config_get(path: str = "", source: str = "effective") -> dict[str, Any]:
        if source not in {"effective", "raw"}:
            return {"ok": False, "error": "source must be either 'effective' or 'raw'"}
        try:
            data = (
                _effective_config(resolved_config_path)
                if source == "effective"
                else _load_raw_config(resolved_config_path)
            )
        except Exception as exc:
            return {
                "ok": False,
                "config_path": str(resolved_config_path),
                "path": path,
                "source": source,
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            value = _get_value(data, path)
        except (KeyError, ValueError) as exc:
            return {
                "ok": False,
                "config_path": str(resolved_config_path),
                "path": path,
                "source": source,
                "error": str(exc),
            }
        return {
            "ok": True,
            "config_path": str(resolved_config_path),
            "path": path,
            "source": source,
            "value": value,
            "agent_runtime": _safe_agent_runtime_summary(resolved_config_path),
        }

    async def config_validate() -> dict[str, Any]:
        try:
            parsed = load_config(resolved_config_path)
        except Exception as exc:
            return {
                "ok": False,
                "config_path": str(resolved_config_path),
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "ok": True,
            "config_path": str(resolved_config_path),
            "domain": parsed.domain,
            "agent_configured": parsed.agent is not None,
            "gateway_count": len(parsed.gateways),
            "agent_runtime": _agent_runtime_summary(parsed),
        }

    async def config_patch(
        updates: list[dict[str, Any]],
        dry_run: bool = True,
        reason: str = "",
    ) -> dict[str, Any]:
        if not updates:
            return {"ok": False, "error": "updates must contain at least one patch"}

        raw = _load_raw_config(resolved_config_path)
        updated = deepcopy(raw)
        changes = []
        try:
            before_runtime = _load_agent_runtime_summary(resolved_config_path)
            for update in updates:
                path = str(update.get("path", ""))
                if "value" not in update:
                    raise ValueError(f"Patch for {path!r} is missing value")
                after = update["value"]
                try:
                    before = _get_value(updated, path)
                except KeyError:
                    before = None
                actual_before = _set_value(updated, path, after)
                changes.append(
                    {
                        "path": path,
                        "before": before if before is not None else actual_before,
                        "after": after,
                    }
                )
            after_runtime = _validate_and_summarise_config_data(resolved_config_path, updated)
        except Exception as exc:
            return {
                "ok": False,
                "config_path": str(resolved_config_path),
                "dry_run": dry_run,
                "error": f"{type(exc).__name__}: {exc}",
            }

        response: dict[str, Any] = {
            "ok": True,
            "config_path": str(resolved_config_path),
            "dry_run": dry_run,
            "reason": reason,
            "changes": changes,
            "restart_required": True,
            "agent_runtime_before": before_runtime,
            "agent_runtime_after": after_runtime,
        }
        if dry_run:
            return response

        backup_path = resolved_config_path.with_name(
            f"{resolved_config_path.name}.bak.{_timestamp()}"
        )
        shutil.copy2(resolved_config_path, backup_path)
        resolved_config_path.write_text(_dump_config(updated), encoding="utf-8")
        response["backup_path"] = str(backup_path)
        return response

    async def request_runtime_restart(reason: str = "", mode: str = "restart") -> dict[str, Any]:
        if mode not in {"restart", "reload"}:
            return {"ok": False, "error": "mode must be either 'restart' or 'reload'"}
        marker_dir = resolved_base_dir / ".evidune"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_path = marker_dir / "restart-request.json"
        payload = {
            "kind": "runtime_restart_request",
            "status": "requested",
            "mode": mode,
            "reason": reason,
            "pid": os.getpid(),
            "config_path": str(resolved_config_path),
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "mode": mode,
            "marker_path": str(marker_path),
            "message": "Restart requested; a supervisor or operator must act on this marker.",
        }

    return [
        Tool(
            name="config_get",
            description=(
                "Read effective active evidune.yaml config by default, including loader "
                "defaults. Use path agent.llm_provider or agent.llm_model to inspect the "
                "current configured model; set source='raw' to read only the YAML text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "e.g. agent.llm_model"},
                    "source": {
                        "type": "string",
                        "enum": ["effective", "raw"],
                        "default": "effective",
                    },
                },
            },
            handler=config_get,
        ),
        Tool(
            name="config_validate",
            description=(
                "Validate active evidune.yaml and return the effective agent runtime summary, "
                "including configured LLM provider and model."
            ),
            parameters={"type": "object", "properties": {}},
            handler=config_validate,
        ),
        Tool(
            name="config_patch",
            description=(
                "Apply validated dotted-path patches to evidune.yaml. Defaults to dry_run. "
                "Model/provider patches such as agent.llm_model and agent.llm_provider are "
                "validated and reported with before/after runtime summaries."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "value": {"description": "Any JSON-compatible replacement value."},
                            },
                            "required": ["path", "value"],
                        },
                    },
                    "dry_run": {"type": "boolean", "default": True},
                    "reason": {"type": "string"},
                },
                "required": ["updates"],
            },
            handler=config_patch,
        ),
        Tool(
            name="request_runtime_restart",
            description=("Request a controlled restart or reload by writing a marker."),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "mode": {"type": "string", "enum": ["restart", "reload"], "default": "restart"},
                },
            },
            handler=request_runtime_restart,
        ),
    ]
