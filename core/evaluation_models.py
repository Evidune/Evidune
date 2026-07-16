"""Shared immutable models for Skill evaluation experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from skills.governance import text_digest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class VariantSpec:
    name: str
    version: str
    content: str
    mutation_operator: str = ""

    @property
    def digest(self) -> str:
        return text_digest(self.content)


@dataclass(frozen=True)
class ExperimentRunSummary:
    experiment_id: str
    corpus_id: str
    split: str
    status: str
    artifact_dir: str
    planned_trials: int
    valid_trials: int
    invalid_trials: int
    variant_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    governance: dict[str, Any] = field(default_factory=dict)
    early_stop: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
