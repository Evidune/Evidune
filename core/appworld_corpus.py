"""Generate a pinned EvaluationCorpus manifest from an installed AppWorld dataset."""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def build_appworld_manifest(
    *,
    dataset: str,
    split: str,
    source_commit: str,
    limit: int = 30,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_commit):
        raise ValueError("AppWorld import requires a full 40-character --source-commit")
    if split not in {"development", "holdout", "security_holdout"}:
        raise ValueError("AppWorld corpus split must be development, holdout, or security_holdout")
    if limit < 1 or limit > 1000:
        raise ValueError("AppWorld corpus --limit must be between 1 and 1000")
    try:
        from appworld import load_task_ids
    except ImportError as exc:
        raise ValueError(
            "AppWorld is not installed; use Python 3.11+ and install evidune[benchmarks]"
        ) from exc
    task_ids = [str(task_id) for task_id in load_task_ids(dataset)[:limit]]
    if not task_ids:
        raise ValueError(f"AppWorld dataset has no tasks: {dataset}")
    try:
        package_version = importlib.metadata.version("appworld")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    task_set_digest = hashlib.sha256("\n".join(task_ids).encode()).hexdigest()[:12]
    corpus_id = f"appworld-{dataset}-{source_commit[:12]}-{task_set_digest}"
    return {
        "corpus_id": corpus_id,
        "schema_version": 1,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "adapter": {"id": "appworld", "revision": "v1"},
        "sources": [
            {
                "name": "appworld",
                "mode": "git",
                "url": "https://github.com/StonyBrookNLP/appworld.git",
                "commit": source_commit.lower(),
                "license": "Apache-2.0",
                "paths": ["README.md"],
            }
        ],
        "tasks": [
            {
                "id": task_id,
                "instruction": "__loaded_by_appworld_adapter__",
                "metadata": {"appworld_task_id": task_id, "dataset": dataset},
            }
            for task_id in task_ids
        ],
        "splits": {split: task_ids},
        "environment": {
            "kind": "appworld",
            "package_version": package_version,
            "experiment_name": corpus_id,
            "protected_data": True,
        },
        "network_policy": {"mode": "deny", "allowed_hosts": []},
        "evaluator": {
            "holdout_visibility": "hidden",
            "required_evaluators": ["appworld_state_evaluator"],
            "minimum_attribution": "direct",
            "minimum_valid_trials": 3,
            "require_live_model": True,
            "allow_non_holdout_promotion": False,
        },
        "budget": {
            "max_model_calls": len(task_ids) * 2 * 3 * 18,
            "max_model_turns_per_trial": 18,
            "max_tool_calls_per_trial": 30,
            "model_call_timeout_seconds": 120,
        },
    }


def write_appworld_manifest(
    path: Path,
    *,
    dataset: str,
    split: str,
    source_commit: str,
    limit: int = 30,
) -> dict[str, Any]:
    if path.exists():
        raise ValueError(f"Refusing to overwrite existing corpus manifest: {path}")
    payload = build_appworld_manifest(
        dataset=dataset,
        split=split,
        source_commit=source_commit,
        limit=limit,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return payload
