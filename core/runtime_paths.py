"""Resolve runtime file paths relative to the active config directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import AiflayConfig


def resolve_runtime_path(path: str, base_dir: Path) -> str:
    """Resolve a config path against the config directory when needed."""
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((base_dir / expanded).resolve())


def resolve_memory_path(config: AiflayConfig, base_dir: Path) -> str:
    return resolve_runtime_path(config.memory.path, base_dir)


def resolve_emergence_output_dir(config: AiflayConfig, base_dir: Path) -> str:
    if not config.agent:
        return ""
    return resolve_runtime_path(config.agent.emergence.output_dir, base_dir)


def resolve_runtime_dir(config: AiflayConfig, base_dir: Path) -> str:
    if not config.agent:
        return ""
    return resolve_runtime_path(config.agent.harness.environment.runtime_dir, base_dir)


def resolve_metrics_config(config: AiflayConfig, base_dir: Path) -> dict[str, Any]:
    """Return adapter config with relative file paths resolved for runtime use."""
    resolved = dict(config.metrics.config)
    file_path = resolved.get("file")
    if isinstance(file_path, str) and file_path.strip():
        resolved["file"] = resolve_runtime_path(file_path, base_dir)
    return resolved
