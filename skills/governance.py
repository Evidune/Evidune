"""Typed, score-optional governance models for Skill executions.

The models in this module are deliberately storage- and orchestrator-neutral.  A
benchmark adapter, a live Agent execution, and a delayed outcome probe all emit the same
``EvaluationResult`` envelope.  Lifecycle decisions use verdicts and hard gates first;
native numeric measurements remain optional diagnostic evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EvaluationVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    CENSORED = "censored"
    INVALID = "invalid"


class AttributionGrade(str, Enum):
    DIRECT = "direct"
    CONTROLLED = "controlled"
    SUPPORTED = "supported"
    OBSERVATIONAL = "observational"
    UNKNOWN = "unknown"


_ATTRIBUTION_RANK = {
    AttributionGrade.UNKNOWN: 0,
    AttributionGrade.OBSERVATIONAL: 1,
    AttributionGrade.SUPPORTED: 2,
    AttributionGrade.CONTROLLED: 3,
    AttributionGrade.DIRECT: 4,
}


def canonical_digest(value: Any) -> str:
    """Return a content-addressed SHA-256 digest for JSON-compatible data."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_verdict(value: EvaluationVerdict | str) -> EvaluationVerdict:
    if isinstance(value, EvaluationVerdict):
        return value
    return EvaluationVerdict(str(value).strip().lower())


def _as_attribution(value: AttributionGrade | str) -> AttributionGrade:
    if isinstance(value, AttributionGrade):
        return value
    return AttributionGrade(str(value or "unknown").strip().lower())


@dataclass(frozen=True)
class EvaluationResult:
    evaluator_id: str
    evaluator_revision: str
    evaluator_type: str
    verdict: EvaluationVerdict | str
    execution_id: int = 0
    skill_name: str = ""
    skill_version: str = ""
    contract_digest: str = ""
    score: float | None = None
    uncertainty: str = "unknown"
    dimensions: dict[str, Any] = field(default_factory=dict)
    failure_modes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    hard_gate_failures: list[str] = field(default_factory=list)
    attribution_grade: AttributionGrade | str = AttributionGrade.UNKNOWN
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evaluator_id.strip():
            raise ValueError("evaluator_id must be non-empty")
        if not self.evaluator_revision.strip():
            raise ValueError("evaluator_revision must be non-empty")
        if not self.evaluator_type.strip():
            raise ValueError("evaluator_type must be non-empty")
        object.__setattr__(self, "verdict", _as_verdict(self.verdict))
        object.__setattr__(self, "attribution_grade", _as_attribution(self.attribution_grade))
        if self.score is not None and not math.isfinite(float(self.score)):
            raise ValueError("score must be finite when present")

    @property
    def advisory(self) -> bool:
        return self.evaluator_type == "llm_judge" or bool(self.metadata.get("advisory"))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        payload["attribution_grade"] = self.attribution_grade.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvaluationResult:
        return cls(**payload)


@dataclass(frozen=True)
class GovernancePolicy:
    """Deterministic lifecycle gate over typed evaluator output."""

    minimum_attribution: AttributionGrade | str = AttributionGrade.UNKNOWN
    required_evaluators: list[str] = field(default_factory=list)
    require_authoritative_result: bool = True
    block_on_any_authoritative_failure: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_attribution", _as_attribution(self.minimum_attribution))


@dataclass(frozen=True)
class GovernanceDecision:
    verdict: EvaluationVerdict
    promotable: bool
    hard_gate_failures: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    missing_evaluators: list[str] = field(default_factory=list)
    contributing_evaluator_ids: list[str] = field(default_factory=list)
    advisory_evaluator_ids: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        return payload


def decide_governance(
    results: Iterable[EvaluationResult],
    policy: GovernancePolicy | None = None,
) -> GovernanceDecision:
    """Apply hard gates and typed verdict precedence without averaging scores."""
    policy = policy or GovernancePolicy()
    items = list(results)
    advisory = [item for item in items if item.advisory]
    authoritative = [item for item in items if not item.advisory]
    present_ids = {item.evaluator_id for item in authoritative}
    missing = [item for item in policy.required_evaluators if item not in present_ids]
    hard_gate_failures = sorted(
        {failure for item in authoritative for failure in item.hard_gate_failures if failure}
    )
    failure_modes = sorted(
        {failure for item in authoritative for failure in item.failure_modes if failure}
    )

    common = {
        "hard_gate_failures": hard_gate_failures,
        "failure_modes": failure_modes,
        "missing_evaluators": missing,
        "contributing_evaluator_ids": [item.evaluator_id for item in authoritative],
        "advisory_evaluator_ids": [item.evaluator_id for item in advisory],
    }
    if hard_gate_failures:
        return GovernanceDecision(
            EvaluationVerdict.FAIL,
            False,
            reason="hard gate failure",
            **common,
        )
    if missing:
        return GovernanceDecision(
            EvaluationVerdict.INCONCLUSIVE,
            False,
            reason="required evaluator results are missing",
            **common,
        )
    if policy.require_authoritative_result and not authoritative:
        return GovernanceDecision(
            EvaluationVerdict.INCONCLUSIVE,
            False,
            reason="no authoritative evaluator result",
            **common,
        )

    eligible = [
        item
        for item in authoritative
        if _ATTRIBUTION_RANK[item.attribution_grade]
        >= _ATTRIBUTION_RANK[policy.minimum_attribution]
    ]
    if authoritative and not eligible:
        return GovernanceDecision(
            EvaluationVerdict.INCONCLUSIVE,
            False,
            reason="attribution grade is below policy minimum",
            **common,
        )
    if policy.block_on_any_authoritative_failure and any(
        item.verdict == EvaluationVerdict.FAIL for item in eligible
    ):
        return GovernanceDecision(
            EvaluationVerdict.FAIL,
            False,
            reason="authoritative evaluator failure",
            **common,
        )
    unresolved = {
        EvaluationVerdict.INCONCLUSIVE,
        EvaluationVerdict.CENSORED,
        EvaluationVerdict.INVALID,
    }
    if not eligible or any(item.verdict in unresolved for item in eligible):
        return GovernanceDecision(
            EvaluationVerdict.INCONCLUSIVE,
            False,
            reason="authoritative evidence is unresolved",
            **common,
        )
    if all(item.verdict == EvaluationVerdict.PASS for item in eligible):
        return GovernanceDecision(
            EvaluationVerdict.PASS,
            True,
            reason="all authoritative gates passed",
            **common,
        )
    return GovernanceDecision(
        EvaluationVerdict.INCONCLUSIVE,
        False,
        reason="evaluation results do not support promotion",
        **common,
    )
