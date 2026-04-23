"""Skill-specific evaluation contract models and helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class EvaluationCriterion:
    name: str
    description: str
    weight: float = 1.0


@dataclass(frozen=True)
class ObservableMetric:
    name: str
    description: str
    source: str = "execution"
    weight: float = 1.0


@dataclass(frozen=True)
class EvaluationContract:
    version: int = 1
    criteria: list[EvaluationCriterion] = field(default_factory=list)
    observable_metrics: list[ObservableMetric] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    min_pass_score: float = 0.7
    rewrite_below_score: float = 0.55
    disable_below_score: float = 0.25
    min_samples_for_rewrite: int = 3
    min_samples_for_disable: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _clean_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _float_between(value: Any, default: float, *, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)


def _parse_criteria(raw: Any) -> list[EvaluationCriterion]:
    criteria: list[EvaluationCriterion] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                continue
            name = _clean_name(item.get("name"), f"criterion_{idx}")
            description = str(item.get("description") or name.replace("_", " ")).strip()
            criteria.append(
                EvaluationCriterion(
                    name=name,
                    description=description,
                    weight=max(0.0, _float_between(item.get("weight"), 1.0, high=1000.0)),
                )
            )
    return criteria


def _parse_observables(raw: Any) -> list[ObservableMetric]:
    metrics: list[ObservableMetric] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                continue
            name = _clean_name(item.get("name"), f"observable_{idx}")
            description = str(item.get("description") or name.replace("_", " ")).strip()
            metrics.append(
                ObservableMetric(
                    name=name,
                    description=description,
                    source=str(item.get("source") or "execution").strip() or "execution",
                    weight=max(0.0, _float_between(item.get("weight"), 1.0, high=1000.0)),
                )
            )
    return metrics


def normalise_contract(contract: EvaluationContract) -> EvaluationContract:
    criteria = list(contract.criteria)
    if not criteria:
        criteria = default_contract_for_skill("", "").criteria
    if not any(item.weight > 0 for item in criteria):
        criteria = [EvaluationCriterion(item.name, item.description, 1.0) for item in criteria]
    return EvaluationContract(
        version=max(1, int(contract.version or 1)),
        criteria=criteria,
        observable_metrics=list(contract.observable_metrics),
        failure_modes=[str(item).strip() for item in contract.failure_modes if str(item).strip()],
        min_pass_score=_float_between(contract.min_pass_score, 0.7),
        rewrite_below_score=_float_between(contract.rewrite_below_score, 0.55),
        disable_below_score=_float_between(contract.disable_below_score, 0.25),
        min_samples_for_rewrite=max(1, int(contract.min_samples_for_rewrite or 3)),
        min_samples_for_disable=max(1, int(contract.min_samples_for_disable or 2)),
    )


def parse_evaluation_contract(raw: Any) -> EvaluationContract | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, EvaluationContract):
        return normalise_contract(raw)
    if isinstance(raw, str):
        try:
            raw = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
    if not isinstance(raw, dict):
        return None
    contract = EvaluationContract(
        version=_positive_int(raw.get("version", 1), 1),
        criteria=_parse_criteria(raw.get("criteria")),
        observable_metrics=_parse_observables(raw.get("observable_metrics")),
        failure_modes=[
            str(item).strip() for item in (raw.get("failure_modes") or []) if str(item).strip()
        ],
        min_pass_score=_float_between(raw.get("min_pass_score"), 0.7),
        rewrite_below_score=_float_between(raw.get("rewrite_below_score"), 0.55),
        disable_below_score=_float_between(raw.get("disable_below_score"), 0.25),
        min_samples_for_rewrite=_positive_int(raw.get("min_samples_for_rewrite", 3), 3),
        min_samples_for_disable=_positive_int(raw.get("min_samples_for_disable", 2), 2),
    )
    return normalise_contract(contract)


def default_contract_for_skill(name: str, description: str = "") -> EvaluationContract:
    target = description or name or "the skill"
    return EvaluationContract(
        criteria=[
            EvaluationCriterion(
                name="goal_completion",
                description=f"The response completes the intended outcome for {target}.",
                weight=0.4,
            ),
            EvaluationCriterion(
                name="instruction_following",
                description="The response follows the skill instructions and avoids anti-triggers.",
                weight=0.25,
            ),
            EvaluationCriterion(
                name="evidence_quality",
                description=(
                    "Claims are backed by user-provided data, tool output, or explicit "
                    "uncertainty when verification is unavailable."
                ),
                weight=0.25,
            ),
            EvaluationCriterion(
                name="safety_boundary",
                description="The response respects configured tool and execution boundaries.",
                weight=0.1,
            ),
        ],
        observable_metrics=[
            ObservableMetric(
                name="tool_verification_used",
                description="Relevant tool evidence was used when verification was possible.",
                source="tool_trace",
                weight=0.2,
            ),
            ObservableMetric(
                name="user_feedback_signal",
                description="Explicit user feedback supports or rejects the output.",
                source="feedback",
                weight=0.2,
            ),
        ],
        failure_modes=[
            "hallucinated_external_state",
            "skipped_required_verification",
            "ignored_skill_instructions",
        ],
    )


def contract_summary(contract: EvaluationContract | None) -> dict[str, Any] | None:
    if contract is None:
        return None
    return {
        "version": contract.version,
        "criteria": [item.name for item in contract.criteria],
        "observable_metrics": [item.name for item in contract.observable_metrics],
        "failure_modes": list(contract.failure_modes),
        "min_pass_score": contract.min_pass_score,
        "rewrite_below_score": contract.rewrite_below_score,
        "disable_below_score": contract.disable_below_score,
        "min_samples_for_rewrite": contract.min_samples_for_rewrite,
        "min_samples_for_disable": contract.min_samples_for_disable,
    }


def upsert_contract_frontmatter(content: str, contract: EvaluationContract) -> str:
    payload = contract.to_dict()
    match = _FRONTMATTER_RE.match(content)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end() :].lstrip("\n")
    else:
        frontmatter = {}
        body = content.lstrip("\n")
    frontmatter["evaluation_contract"] = payload
    dumped = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n\n{body.rstrip()}\n"
