"""Pinned evaluation-corpus models, loading, and deterministic validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from skills.governance import canonical_digest


@dataclass(frozen=True)
class CorpusSource:
    name: str
    url: str
    commit: str
    license: str
    paths: list[str] = field(default_factory=list)
    digest: str = ""
    mode: str = "local"


@dataclass(frozen=True)
class CorpusTask:
    id: str
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)
    initial_state: dict[str, Any] = field(default_factory=dict)
    expected_state: dict[str, Any] = field(default_factory=dict)
    forbidden_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillTaskPairing:
    skill: str
    tasks: list[str]
    non_applicable_tasks: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    tool_compatibility: dict[str, Any] = field(default_factory=dict)
    shim: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationCorpus:
    corpus_id: str
    schema_version: int
    adapter: str
    adapter_revision: str
    sources: list[CorpusSource]
    tasks: dict[str, CorpusTask]
    splits: dict[str, list[str]]
    pairings: list[SkillTaskPairing] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    evaluator: dict[str, Any] = field(default_factory=dict)
    network_policy: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    manifest_path: str = ""
    manifest_digest: str = ""

    def task_refs(self, split: str, *, skill_name: str = "") -> list[CorpusTask]:
        if split not in self.splits:
            raise ValueError(f"Unknown corpus split: {split}")
        task_ids = list(self.splits[split])
        if skill_name and self.pairings:
            matching = [pairing for pairing in self.pairings if pairing.skill == skill_name]
            if not matching:
                raise ValueError(f"No reviewed task pairing exists for Skill {skill_name}")
            applicable = {task_id for pairing in matching for task_id in pairing.tasks}
            task_ids = [task_id for task_id in task_ids if task_id in applicable]
            if not task_ids:
                raise ValueError(f"Skill {skill_name} has no applicable tasks in split {split}")
        return [self.tasks[task_id] for task_id in task_ids]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tasks"] = {task_id: asdict(task) for task_id, task in self.tasks.items()}
        return payload


@dataclass(frozen=True)
class CorpusValidation:
    ok: bool
    corpus_id: str
    manifest_digest: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    task_count: int = 0
    split_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_sources(raw: Any) -> list[CorpusSource]:
    if not isinstance(raw, list):
        return []
    return [
        CorpusSource(
            name=str(item.get("name") or "").strip(),
            url=str(item.get("url") or "").strip(),
            commit=str(item.get("commit") or "").strip(),
            license=str(item.get("license") or "").strip(),
            paths=[str(path) for path in item.get("paths", [])],
            digest=str(item.get("digest") or "").strip(),
            mode=str(item.get("mode") or "local").strip(),
        )
        for item in raw
        if isinstance(item, dict)
    ]


def _parse_tasks(raw: Any) -> dict[str, CorpusTask]:
    tasks: dict[str, CorpusTask] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        task_id = str(item["id"]).strip()
        tasks[task_id] = CorpusTask(
            id=task_id,
            instruction=str(item.get("instruction") or "").strip(),
            metadata=dict(item.get("metadata") or {}),
            initial_state=dict(item.get("initial_state") or {}),
            expected_state=dict(item.get("expected_state") or {}),
            forbidden_state=dict(item.get("forbidden_state") or {}),
        )
    return tasks


def _parse_pairings(raw: Any) -> list[SkillTaskPairing]:
    return [
        SkillTaskPairing(
            skill=str(item.get("skill") or "").strip(),
            tasks=[str(task) for task in item.get("tasks", [])],
            non_applicable_tasks=[str(task) for task in item.get("non_applicable_tasks", [])],
            required_capabilities=[str(value) for value in item.get("required_capabilities", [])],
            tool_compatibility=dict(item.get("tool_compatibility") or {}),
            shim=dict(item.get("shim") or {}),
        )
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict)
    ]


def load_evaluation_corpus(path: str | Path) -> EvaluationCorpus:
    manifest_path = Path(path).resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Corpus manifest root must be a mapping")
    adapter = raw.get("adapter") or {}
    if isinstance(adapter, str):
        adapter = {"id": adapter, "revision": "v1"}
    splits_raw = raw.get("splits") or {}
    return EvaluationCorpus(
        corpus_id=str(raw.get("corpus_id") or "").strip(),
        schema_version=int(raw.get("schema_version") or 1),
        adapter=str(adapter.get("id") or "").strip(),
        adapter_revision=str(adapter.get("revision") or "").strip(),
        sources=_parse_sources(raw.get("sources")),
        tasks=_parse_tasks(raw.get("tasks")),
        splits={
            str(name): [str(task_id) for task_id in values]
            for name, values in splits_raw.items()
            if isinstance(values, list)
        },
        pairings=_parse_pairings(raw.get("pairings")),
        environment=dict(raw.get("environment") or {}),
        evaluator=dict(raw.get("evaluator") or {}),
        network_policy=dict(raw.get("network_policy") or {}),
        budget=dict(raw.get("budget") or {}),
        manifest_path=str(manifest_path),
        manifest_digest=canonical_digest(raw),
    )


def corpus_source_root(corpus: EvaluationCorpus, base_dir: Path) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", corpus.corpus_id).strip("-") or "corpus"
    return base_dir.resolve() / ".evidune" / "corpora" / safe_id / "sources"


def source_checkout_path(source: CorpusSource, source_root: Path) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", source.name).strip("-")
    if not safe_name:
        raise ValueError("source name cannot be converted into a safe checkout path")
    return source_root.resolve() / safe_name


def validate_evaluation_corpus(
    corpus: EvaluationCorpus, *, source_root: Path | None = None
) -> CorpusValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if not corpus.corpus_id:
        errors.append("corpus_id is required")
    if corpus.schema_version != 1:
        errors.append(f"unsupported schema_version: {corpus.schema_version}")
    if not corpus.adapter or not corpus.adapter_revision:
        errors.append("adapter id and revision are required")
    if not corpus.sources:
        errors.append("at least one pinned source is required")
    for source in corpus.sources:
        if not source.name or not source.url or not source.commit or not source.license:
            errors.append(f"source {source.name or '<unnamed>'} lacks url, commit, or license")
        if source.mode not in {"local", "git"}:
            errors.append(f"source {source.name or '<unnamed>'} has unsupported mode {source.mode}")
        if source.mode == "git" and not re.fullmatch(r"[0-9a-fA-F]{40}", source.commit):
            errors.append(f"git source {source.name or '<unnamed>'} requires a full commit SHA")
        for raw_path in source.paths:
            if source.mode == "git":
                if source_root is None:
                    errors.append(f"git source root is required to verify {source.name}")
                    continue
                checkout = source_checkout_path(source, source_root).resolve()
                path = (checkout / raw_path).resolve()
                if not path.is_relative_to(checkout):
                    errors.append(f"source path escapes checkout: {raw_path}")
                    continue
            else:
                path = (Path(corpus.manifest_path).parent / raw_path).resolve()
            if not path.is_file():
                errors.append(f"source path is missing: {raw_path}")
            elif source.digest and len(source.paths) == 1:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != source.digest:
                    errors.append(f"source digest mismatch: {raw_path}")
    _validate_tasks_and_pairings(corpus, errors, warnings)
    return CorpusValidation(
        ok=not errors,
        corpus_id=corpus.corpus_id,
        manifest_digest=corpus.manifest_digest,
        errors=errors,
        warnings=warnings,
        task_count=len(corpus.tasks),
        split_counts={name: len(values) for name, values in corpus.splits.items()},
    )


def _validate_tasks_and_pairings(
    corpus: EvaluationCorpus, errors: list[str], warnings: list[str]
) -> None:
    if not corpus.tasks:
        errors.append("at least one task is required")
    for task in corpus.tasks.values():
        if not task.instruction:
            errors.append(f"task {task.id} has no instruction")
    seen: dict[str, str] = {}
    for split, task_ids in corpus.splits.items():
        for task_id in task_ids:
            if task_id not in corpus.tasks:
                errors.append(f"split {split} references unknown task {task_id}")
            if task_id in seen:
                errors.append(f"task {task_id} appears in both {seen[task_id]} and {split}")
            seen[task_id] = split
    unassigned = sorted(set(corpus.tasks) - set(seen))
    if unassigned:
        warnings.append(f"tasks not assigned to a split: {', '.join(unassigned)}")
    for pairing in corpus.pairings:
        if not pairing.skill:
            errors.append("pairing requires a skill")
        for task_id in pairing.tasks + pairing.non_applicable_tasks:
            if task_id not in corpus.tasks:
                errors.append(f"pairing {pairing.skill} references unknown task {task_id}")
        if pairing.shim and not pairing.tool_compatibility:
            errors.append(f"pairing {pairing.skill} has a shim without tool_compatibility")
    names = [pairing.skill for pairing in corpus.pairings]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate Skill pairings: {', '.join(duplicates)}")
    if "holdout" in corpus.splits:
        visibility = str(corpus.evaluator.get("holdout_visibility") or "hidden")
        if visibility != "hidden":
            errors.append("holdout evaluator visibility must be hidden")
