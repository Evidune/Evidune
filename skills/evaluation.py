"""Skill contract models and helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class ExecutionCriterion:
    name: str
    description: str
    weight: float = 1.0


@dataclass(frozen=True)
class ObservableSignal:
    name: str
    description: str
    source: str = "execution"
    weight: float = 1.0


@dataclass(frozen=True, init=False)
class ExecutionContract:
    version: int = 1
    criteria: list[ExecutionCriterion] = field(default_factory=list)
    observable_signals: list[ObservableSignal] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    min_pass_score: float = 0.7
    rewrite_below_score: float = 0.55
    disable_below_score: float = 0.25
    min_samples_for_rewrite: int = 3
    min_samples_for_disable: int = 2

    def __init__(
        self,
        version: int = 1,
        criteria: list[ExecutionCriterion] | None = None,
        observable_signals: list[ObservableSignal] | None = None,
        failure_modes: list[str] | None = None,
        min_pass_score: float = 0.7,
        rewrite_below_score: float = 0.55,
        disable_below_score: float = 0.25,
        min_samples_for_rewrite: int = 3,
        min_samples_for_disable: int = 2,
        observable_metrics: list[ObservableSignal] | None = None,
    ) -> None:
        signals = observable_signals if observable_signals is not None else observable_metrics
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "criteria", list(criteria or []))
        object.__setattr__(self, "observable_signals", list(signals or []))
        object.__setattr__(self, "failure_modes", list(failure_modes or []))
        object.__setattr__(self, "min_pass_score", min_pass_score)
        object.__setattr__(self, "rewrite_below_score", rewrite_below_score)
        object.__setattr__(self, "disable_below_score", disable_below_score)
        object.__setattr__(self, "min_samples_for_rewrite", min_samples_for_rewrite)
        object.__setattr__(self, "min_samples_for_disable", min_samples_for_disable)

    @property
    def observable_metrics(self) -> list[ObservableSignal]:
        """Backward-compatible alias for older call sites."""
        return self.observable_signals

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeWindow:
    current_days: int = 7
    baseline_days: int = 7

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeRewritePolicy:
    target: float | None = None
    min_delta: float = 0.05
    require_segment: bool = True
    severe_regression_delta: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeRollbackPolicy:
    max_negative_delta: float = 0.1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeReferenceUpdatePolicy:
    max_segments: int = 3
    max_exemplars: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeContract:
    entity: str = "artifact"
    primary_kpi: str = ""
    supporting_kpis: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    window: OutcomeWindow = field(default_factory=OutcomeWindow)
    min_sample_size: int = 3
    rewrite_policy: OutcomeRewritePolicy = field(default_factory=OutcomeRewritePolicy)
    rollback_policy: OutcomeRollbackPolicy = field(default_factory=OutcomeRollbackPolicy)
    reference_update_policy: OutcomeReferenceUpdatePolicy = field(
        default_factory=OutcomeReferenceUpdatePolicy
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "primary_kpi": self.primary_kpi,
            "supporting_kpis": list(self.supporting_kpis),
            "dimensions": list(self.dimensions),
            "window": self.window.to_dict(),
            "min_sample_size": self.min_sample_size,
            "rewrite_policy": self.rewrite_policy.to_dict(),
            "rollback_policy": self.rollback_policy.to_dict(),
            "reference_update_policy": self.reference_update_policy.to_dict(),
        }


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


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _parse_criteria(raw: Any) -> list[ExecutionCriterion]:
    criteria: list[ExecutionCriterion] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                continue
            name = _clean_name(item.get("name"), f"criterion_{idx}")
            description = str(item.get("description") or name.replace("_", " ")).strip()
            criteria.append(
                ExecutionCriterion(
                    name=name,
                    description=description,
                    weight=max(0.0, _float_between(item.get("weight"), 1.0, high=1000.0)),
                )
            )
    return criteria


def _parse_observable_signals(raw: Any) -> list[ObservableSignal]:
    signals: list[ObservableSignal] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                continue
            name = _clean_name(item.get("name"), f"observable_{idx}")
            description = str(item.get("description") or name.replace("_", " ")).strip()
            signals.append(
                ObservableSignal(
                    name=name,
                    description=description,
                    source=str(item.get("source") or "execution").strip() or "execution",
                    weight=max(0.0, _float_between(item.get("weight"), 1.0, high=1000.0)),
                )
            )
    return signals


def default_execution_contract_for_skill(name: str, description: str = "") -> ExecutionContract:
    target = description or name or "the skill"
    return ExecutionContract(
        criteria=[
            ExecutionCriterion(
                name="goal_completion",
                description=f"The response completes the intended outcome for {target}.",
                weight=0.4,
            ),
            ExecutionCriterion(
                name="instruction_following",
                description="The response follows the skill instructions and avoids anti-triggers.",
                weight=0.25,
            ),
            ExecutionCriterion(
                name="evidence_quality",
                description=(
                    "Claims are backed by user-provided data, tool output, or explicit "
                    "uncertainty when verification is unavailable."
                ),
                weight=0.25,
            ),
            ExecutionCriterion(
                name="safety_boundary",
                description="The response respects configured tool and execution boundaries.",
                weight=0.1,
            ),
        ],
        observable_signals=[
            ObservableSignal(
                name="tool_verification_used",
                description="Relevant tool evidence was used when verification was possible.",
                source="tool_trace",
                weight=0.2,
            ),
            ObservableSignal(
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


def normalise_execution_contract(contract: ExecutionContract) -> ExecutionContract:
    criteria = list(contract.criteria)
    if not criteria:
        criteria = default_execution_contract_for_skill("", "").criteria
    if not any(item.weight > 0 for item in criteria):
        criteria = [ExecutionCriterion(item.name, item.description, 1.0) for item in criteria]
    return ExecutionContract(
        version=max(1, int(contract.version or 1)),
        criteria=criteria,
        observable_signals=list(contract.observable_signals),
        failure_modes=_string_list(contract.failure_modes),
        min_pass_score=_float_between(contract.min_pass_score, 0.7),
        rewrite_below_score=_float_between(contract.rewrite_below_score, 0.55),
        disable_below_score=_float_between(contract.disable_below_score, 0.25),
        min_samples_for_rewrite=max(1, int(contract.min_samples_for_rewrite or 3)),
        min_samples_for_disable=max(1, int(contract.min_samples_for_disable or 2)),
    )


def parse_execution_contract(raw: Any) -> ExecutionContract | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, ExecutionContract):
        return normalise_execution_contract(raw)
    if isinstance(raw, str):
        try:
            raw = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
    if not isinstance(raw, dict):
        return None

    signals_raw = raw.get("observable_signals")
    if signals_raw is None:
        signals_raw = raw.get("observable_metrics")

    contract = ExecutionContract(
        version=_positive_int(raw.get("version", 1), 1),
        criteria=_parse_criteria(raw.get("criteria")),
        observable_signals=_parse_observable_signals(signals_raw),
        failure_modes=_string_list(raw.get("failure_modes")),
        min_pass_score=_float_between(raw.get("min_pass_score"), 0.7),
        rewrite_below_score=_float_between(raw.get("rewrite_below_score"), 0.55),
        disable_below_score=_float_between(raw.get("disable_below_score"), 0.25),
        min_samples_for_rewrite=_positive_int(raw.get("min_samples_for_rewrite", 3), 3),
        min_samples_for_disable=_positive_int(raw.get("min_samples_for_disable", 2), 2),
    )
    return normalise_execution_contract(contract)


def _parse_window(raw: Any) -> OutcomeWindow:
    if not isinstance(raw, dict):
        return OutcomeWindow()
    return OutcomeWindow(
        current_days=_positive_int(raw.get("current_days", 7), 7),
        baseline_days=_positive_int(raw.get("baseline_days", 7), 7),
    )


def _parse_rewrite_policy(raw: Any) -> OutcomeRewritePolicy:
    if not isinstance(raw, dict):
        return OutcomeRewritePolicy()
    target = raw.get("target")
    target_value = None
    if target is not None:
        try:
            target_value = float(target)
        except (TypeError, ValueError):
            target_value = None
    require_segment = raw.get("require_segment", True)
    return OutcomeRewritePolicy(
        target=target_value,
        min_delta=max(0.0, _float_between(raw.get("min_delta"), 0.05, high=1000.0)),
        require_segment=bool(require_segment),
        severe_regression_delta=max(
            0.0,
            _float_between(raw.get("severe_regression_delta"), 0.2, high=1000.0),
        ),
    )


def _parse_rollback_policy(raw: Any) -> OutcomeRollbackPolicy:
    if not isinstance(raw, dict):
        return OutcomeRollbackPolicy()
    return OutcomeRollbackPolicy(
        max_negative_delta=max(
            0.0,
            _float_between(raw.get("max_negative_delta"), 0.1, high=1000.0),
        )
    )


def _parse_reference_update_policy(raw: Any) -> OutcomeReferenceUpdatePolicy:
    if not isinstance(raw, dict):
        return OutcomeReferenceUpdatePolicy()
    return OutcomeReferenceUpdatePolicy(
        max_segments=_positive_int(raw.get("max_segments", 3), 3),
        max_exemplars=_positive_int(raw.get("max_exemplars", 2), 2),
    )


def parse_outcome_contract(raw: Any) -> OutcomeContract | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, OutcomeContract):
        return raw if raw.primary_kpi else None
    if isinstance(raw, str):
        try:
            raw = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
    if not isinstance(raw, dict):
        return None
    primary_kpi = str(raw.get("primary_kpi") or "").strip()
    if not primary_kpi:
        return None
    return OutcomeContract(
        entity=str(raw.get("entity") or "artifact").strip() or "artifact",
        primary_kpi=primary_kpi,
        supporting_kpis=_string_list(raw.get("supporting_kpis")),
        dimensions=_string_list(raw.get("dimensions")),
        window=_parse_window(raw.get("window")),
        min_sample_size=_positive_int(raw.get("min_sample_size", 3), 3),
        rewrite_policy=_parse_rewrite_policy(raw.get("rewrite_policy")),
        rollback_policy=_parse_rollback_policy(raw.get("rollback_policy")),
        reference_update_policy=_parse_reference_update_policy(raw.get("reference_update_policy")),
    )


def execution_contract_summary(contract: ExecutionContract | None) -> dict[str, Any] | None:
    if contract is None:
        return None
    observable_names = [item.name for item in contract.observable_signals]
    return {
        "version": contract.version,
        "criteria": [item.name for item in contract.criteria],
        "observable_signals": observable_names,
        "observable_metrics": observable_names,
        "failure_modes": list(contract.failure_modes),
        "min_pass_score": contract.min_pass_score,
        "rewrite_below_score": contract.rewrite_below_score,
        "disable_below_score": contract.disable_below_score,
        "min_samples_for_rewrite": contract.min_samples_for_rewrite,
        "min_samples_for_disable": contract.min_samples_for_disable,
    }


def outcome_contract_summary(contract: OutcomeContract | None) -> dict[str, Any] | None:
    if contract is None:
        return None
    return {
        "entity": contract.entity,
        "primary_kpi": contract.primary_kpi,
        "supporting_kpis": list(contract.supporting_kpis),
        "dimensions": list(contract.dimensions),
        "window": contract.window.to_dict(),
        "min_sample_size": contract.min_sample_size,
        "rewrite_policy": contract.rewrite_policy.to_dict(),
        "rollback_policy": contract.rollback_policy.to_dict(),
        "reference_update_policy": contract.reference_update_policy.to_dict(),
    }


def _upsert_frontmatter_field(content: str, field_name: str, payload: dict[str, Any]) -> str:
    match = _FRONTMATTER_RE.match(content)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end() :].lstrip("\n")
    else:
        frontmatter = {}
        body = content.lstrip("\n")
    frontmatter.pop("evaluation_contract", None)
    frontmatter[field_name] = payload
    dumped = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n\n{body.rstrip()}\n"


def upsert_execution_contract_frontmatter(content: str, contract: ExecutionContract) -> str:
    return _upsert_frontmatter_field(content, "execution_contract", contract.to_dict())


def upsert_outcome_contract_frontmatter(content: str, contract: OutcomeContract) -> str:
    return _upsert_frontmatter_field(content, "outcome_contract", contract.to_dict())


# Backward-compatible aliases used throughout the current codebase.
EvaluationCriterion = ExecutionCriterion
ObservableMetric = ObservableSignal
EvaluationContract = ExecutionContract
default_contract_for_skill = default_execution_contract_for_skill
parse_evaluation_contract = parse_execution_contract
contract_summary = execution_contract_summary
upsert_contract_frontmatter = upsert_execution_contract_frontmatter
