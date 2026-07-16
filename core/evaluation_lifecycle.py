"""Promotion and rollback lifecycle for immutable Skill experiments."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import yaml

from memory.store import MemoryStore
from skills.governance import text_digest

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _skill_identity(content: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError("Promoted Skill content requires YAML frontmatter")
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        raise ValueError("Promoted Skill frontmatter must be a mapping")
    return str(payload.get("name") or ""), str(payload.get("version") or "")


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    if mode is not None:
        temporary.chmod(mode)
    temporary.replace(path)


def promote(memory: MemoryStore, base_dir: Path, args) -> int:
    if not args.experiment_id or not args.skill_path:
        raise ValueError("eval promote requires --experiment-id and --skill-path")
    experiment = memory.get_skill_experiment(args.experiment_id)
    if experiment is None:
        raise ValueError(f"Unknown Skill experiment: {args.experiment_id}")
    if experiment["status"] != "validated":
        raise ValueError("Only a validated candidate can be promoted")
    governance = experiment["evidence"].get("governance") or {}
    if not governance.get("promotable"):
        raise ValueError("Validated candidate is missing a promotable governance decision")
    if not experiment["source_execution_ids"] and not experiment["policy"].get(
        "allow_unattributed_candidate"
    ):
        raise ValueError("Promotion requires source execution ids for candidate provenance")
    for execution_id in experiment["source_execution_ids"]:
        execution = memory.get_skill_executions_by_id(int(execution_id))
        if execution is None or execution["skill_name"] != experiment["skill_name"]:
            raise ValueError("Promotion source execution provenance is invalid")
    allow_non_holdout = bool(experiment["policy"].get("allow_non_holdout_promotion"))
    if experiment["split"] not in {"holdout", "security_holdout"} and not allow_non_holdout:
        raise ValueError("Promotion requires a validated holdout experiment")
    provider = str(experiment["model_ref"].get("provider") or "").strip().lower()
    if experiment["policy"].get("require_live_model") and provider in {
        "",
        "deterministic",
        "fixture",
        "mock",
    }:
        raise ValueError("Promotion policy requires validation with a live model provider")
    skill_path = _resolve(base_dir, args.skill_path)
    current = skill_path.read_text(encoding="utf-8")
    if text_digest(current) != experiment["parent_digest"]:
        raise ValueError("Active Skill no longer matches the experiment parent digest")
    if text_digest(experiment["candidate_content"]) != experiment["candidate_digest"]:
        raise ValueError("Stored candidate content no longer matches its immutable digest")
    candidate_name, candidate_version = _skill_identity(experiment["candidate_content"])
    if candidate_name != experiment["skill_name"]:
        raise ValueError("Candidate Skill name does not match the experiment")
    if candidate_version != experiment["candidate_version"]:
        raise ValueError("Candidate frontmatter version does not match the experiment")
    current_state = memory.get_skill_state(experiment["skill_name"])
    if current_state and current_state.get("path"):
        expected_path = _resolve(base_dir, current_state["path"])
        if expected_path != skill_path:
            raise ValueError("Promotion target does not match the active Skill path")
    _atomic_write(skill_path, experiment["candidate_content"])
    memory.transition_skill_experiment(
        args.experiment_id,
        "promoted",
        reason="All configured promotion gates passed",
        evidence={"skill_path": str(skill_path)},
    )
    memory.upsert_skill_state(
        experiment["skill_name"],
        origin=current_state["origin"] if current_state else "base",
        path=str(skill_path),
        status="active",
        reason="Promoted validated Skill candidate",
        evidence={"experiment_id": args.experiment_id},
    )
    memory.record_skill_lifecycle_event(
        experiment["skill_name"],
        "promote",
        status="active",
        path=str(skill_path),
        reason="Promoted validated Skill candidate",
        evidence={"experiment_id": args.experiment_id},
        content_before=current,
        content_after=experiment["candidate_content"],
    )
    _print_json(
        {"experiment_id": args.experiment_id, "status": "promoted", "path": str(skill_path)}
    )
    return 0


def rollback(memory: MemoryStore, base_dir: Path, args) -> int:
    if not args.experiment_id or not args.skill_path:
        raise ValueError("eval rollback requires --experiment-id and --skill-path")
    experiment = memory.get_skill_experiment(args.experiment_id)
    if experiment is None:
        raise ValueError(f"Unknown Skill experiment: {args.experiment_id}")
    if experiment["status"] != "promoted":
        raise ValueError("Only a promoted candidate can be rolled back")
    skill_path = _resolve(base_dir, args.skill_path)
    current = skill_path.read_text(encoding="utf-8")
    if text_digest(current) != experiment["candidate_digest"]:
        raise ValueError("Active Skill no longer matches the promoted candidate digest")
    _atomic_write(skill_path, experiment["parent_content"])
    reason = args.reason or "Manual rollback"
    memory.transition_skill_experiment(
        args.experiment_id,
        "rolled_back",
        reason=reason,
        evidence={"skill_path": str(skill_path)},
    )
    memory.record_skill_lifecycle_event(
        experiment["skill_name"],
        "rollback",
        status="rolled_back",
        path=str(skill_path),
        reason=reason,
        evidence={"experiment_id": args.experiment_id},
        content_before=current,
        content_after=experiment["parent_content"],
    )
    current_state = memory.get_skill_state(experiment["skill_name"])
    memory.upsert_skill_state(
        experiment["skill_name"],
        origin=current_state["origin"] if current_state else "base",
        path=str(skill_path),
        status="active",
        reason=args.reason or "Rolled back promoted Skill candidate",
        evidence={"experiment_id": args.experiment_id},
    )
    _print_json(
        {"experiment_id": args.experiment_id, "status": "rolled_back", "path": str(skill_path)}
    )
    return 0
