"""Version-specific aggregation of typed Skill evaluation evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

_ATTRIBUTION_RANK = {
    "unknown": 0,
    "observational": 1,
    "supported": 2,
    "controlled": 3,
    "direct": 4,
}


@dataclass(frozen=True)
class VersionEvidenceAggregate:
    skill_name: str
    skill_version: str
    contributing_execution_ids: list[int] = field(default_factory=list)
    excluded_execution_ids: dict[str, list[int]] = field(default_factory=dict)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    attribution_counts: dict[str, int] = field(default_factory=dict)
    failure_mode_counts: dict[str, int] = field(default_factory=dict)
    actionable_failure_modes: dict[str, int] = field(default_factory=dict)
    hard_gate_failures: list[dict[str, Any]] = field(default_factory=list)
    evaluator_revisions: list[str] = field(default_factory=list)
    contract_digests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_version_evidence(
    results: list[dict[str, Any]],
    *,
    skill_name: str,
    skill_version: str,
    minimum_actionable_attribution: str = "supported",
) -> VersionEvidenceAggregate:
    """Aggregate comparable results without collapsing them into a scalar score."""
    threshold = _ATTRIBUTION_RANK.get(minimum_actionable_attribution)
    if threshold is None:
        raise ValueError(f"Unknown attribution grade: {minimum_actionable_attribution}")
    verdicts: Counter[str] = Counter()
    attributions: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    actionable: Counter[str] = Counter()
    contributing: set[int] = set()
    excluded: dict[str, set[int]] = {}
    hard_gates: list[dict[str, Any]] = []
    evaluators: set[str] = set()
    contracts: set[str] = set()

    def exclude(reason: str, execution_id: int) -> None:
        if execution_id:
            excluded.setdefault(reason, set()).add(execution_id)

    for result in results:
        execution_id = int(result.get("execution_id") or 0)
        if result.get("skill_name") != skill_name:
            exclude("different_skill", execution_id)
            continue
        if result.get("skill_version") != skill_version:
            exclude("different_version", execution_id)
            continue
        if result.get("evaluator_type") == "llm_judge" or (result.get("metadata") or {}).get(
            "advisory"
        ):
            exclude("advisory", execution_id)
            continue
        verdict = str(result.get("verdict") or "invalid")
        if verdict in {"invalid", "censored", "inconclusive"}:
            verdicts[verdict] += 1
            exclude(verdict, execution_id)
            continue
        grade = str(result.get("attribution_grade") or "unknown")
        verdicts[verdict] += 1
        attributions[grade] += 1
        if execution_id:
            contributing.add(execution_id)
        evaluators.add(f"{result.get('evaluator_id', '')}@{result.get('evaluator_revision', '')}")
        if result.get("contract_digest"):
            contracts.add(str(result["contract_digest"]))
        for failure in result.get("failure_modes") or []:
            failure = str(failure)
            failures[failure] += 1
            if _ATTRIBUTION_RANK.get(grade, 0) >= threshold:
                actionable[failure] += 1
        for failure in result.get("hard_gate_failures") or []:
            hard_gates.append(
                {
                    "name": str(failure),
                    "execution_id": execution_id,
                    "attribution_grade": grade,
                }
            )
    return VersionEvidenceAggregate(
        skill_name=skill_name,
        skill_version=skill_version,
        contributing_execution_ids=sorted(contributing),
        excluded_execution_ids={
            reason: sorted(execution_ids) for reason, execution_ids in sorted(excluded.items())
        },
        verdict_counts=dict(sorted(verdicts.items())),
        attribution_counts=dict(sorted(attributions.items())),
        failure_mode_counts=dict(sorted(failures.items())),
        actionable_failure_modes=dict(sorted(actionable.items())),
        hard_gate_failures=hard_gates,
        evaluator_revisions=sorted(evaluators),
        contract_digests=sorted(contracts),
    )
