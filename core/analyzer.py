"""Analysis engine: pattern extraction, ranking, and trend detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from core.metrics import MetricRecord, MetricsSnapshot, OutcomeObservation
from skills.evaluation import OutcomeContract


@dataclass
class AnalysisResult:
    """Result of analyzing a metrics snapshot."""

    domain: str
    total_records: int
    top_performers: list[MetricRecord]
    bottom_performers: list[MetricRecord]
    patterns: list[str]
    summary: str
    raw_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutcomeWindowSummary:
    """Outcome KPI summary for one skill over the configured windows."""

    window: dict[str, Any]
    sample_count: int
    baseline_value: float | None
    current_value: float | None
    delta: float | None
    confidence: float
    segment_breakdown: list[dict[str, Any]] = field(default_factory=list)
    policy_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutcomeAnalysisResult:
    """Structured result for run-side outcome governance."""

    skill_name: str
    total_observations: int
    summary: str
    outcome_summary: OutcomeWindowSummary | None = None
    regression_summary: dict[str, Any] = field(default_factory=dict)
    exemplar_slice: list[dict[str, Any]] = field(default_factory=list)
    raw_stats: dict[str, Any] = field(default_factory=dict)


def _sort_by_metric(records: list[MetricRecord], metric: str) -> list[MetricRecord]:
    """Sort records by a specific metric in descending order."""
    return sorted(
        [r for r in records if metric in r.metrics],
        key=lambda r: r.metrics[metric],
        reverse=True,
    )


def _compute_stats(records: list[MetricRecord]) -> dict[str, Any]:
    """Compute aggregate statistics across all records."""
    if not records:
        return {}

    all_metrics: dict[str, list[float]] = {}
    for record in records:
        for key, value in record.metrics.items():
            all_metrics.setdefault(key, []).append(float(value))

    stats: dict[str, Any] = {}
    for key, values in all_metrics.items():
        stats[key] = {
            "total": sum(values),
            "avg": sum(values) / len(values),
            "max": max(values),
            "min": min(values),
            "count": len(values),
        }
    return stats


def _extract_patterns(
    top: list[MetricRecord],
    bottom: list[MetricRecord],
) -> list[str]:
    """Extract observable patterns from top vs bottom performers."""
    patterns = []

    if top and bottom:
        # Title length pattern
        avg_top_title = sum(len(r.title) for r in top) / len(top)
        avg_bottom_title = sum(len(r.title) for r in bottom) / len(bottom)
        if avg_top_title > avg_bottom_title * 1.3:
            patterns.append(
                f"Top performers have longer titles (avg {avg_top_title:.0f} vs {avg_bottom_title:.0f} chars)"
            )
        elif avg_bottom_title > avg_top_title * 1.3:
            patterns.append(
                f"Top performers have shorter titles (avg {avg_top_title:.0f} vs {avg_bottom_title:.0f} chars)"
            )

    return patterns


def analyze(
    snapshot: MetricsSnapshot,
    sort_metric: str = "reads",
    top_n: int = 5,
    bottom_n: int = 3,
) -> AnalysisResult:
    """Analyze a metrics snapshot to find patterns and rank content.

    Args:
        snapshot: The metrics data to analyze.
        sort_metric: The primary metric to rank by.
        top_n: Number of top performers to highlight.
        bottom_n: Number of bottom performers to highlight.

    Returns:
        AnalysisResult with rankings, patterns, and statistics.
    """
    records = snapshot.records
    if not records:
        return AnalysisResult(
            domain=snapshot.domain,
            total_records=0,
            top_performers=[],
            bottom_performers=[],
            patterns=[],
            summary="No records to analyze.",
        )

    sorted_records = _sort_by_metric(records, sort_metric)
    top = sorted_records[:top_n]
    bottom = sorted_records[-bottom_n:] if len(sorted_records) > bottom_n else []

    patterns = _extract_patterns(top, bottom)
    stats = _compute_stats(records)

    total = stats.get(sort_metric, {}).get("total", 0)
    avg = stats.get(sort_metric, {}).get("avg", 0)
    summary = (
        f"{snapshot.domain}: {len(records)} items, "
        f"total {sort_metric}={total:.0f}, avg={avg:.0f}. "
        f"Top: '{top[0].title}' ({top[0].metrics.get(sort_metric, 0)})"
        if top
        else f"{snapshot.domain}: {len(records)} items analyzed."
    )

    return AnalysisResult(
        domain=snapshot.domain,
        total_records=len(records),
        top_performers=top,
        bottom_performers=bottom,
        patterns=patterns,
        summary=summary,
        raw_stats=stats,
    )


def _parse_observed_at(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _sort_outcomes(
    observations: list[OutcomeObservation],
) -> list[tuple[datetime, OutcomeObservation]]:
    ordered: list[tuple[datetime, OutcomeObservation]] = []
    for observation in observations:
        observed_at = _parse_observed_at(observation.timestamp)
        if observed_at is None:
            continue
        ordered.append((observed_at, observation))
    ordered.sort(key=lambda item: item[0], reverse=True)
    return ordered


def _filter_observations(
    snapshot: MetricsSnapshot,
    *,
    skill_name: str,
) -> list[OutcomeObservation]:
    observations = snapshot.observations or []
    if not skill_name:
        return list(observations)
    filtered = [
        item for item in observations if not item.skill_name or item.skill_name == skill_name
    ]
    return filtered


def _average_metric(observations: list[OutcomeObservation], metric: str) -> float | None:
    values = []
    for observation in observations:
        value = observation.metrics.get(metric)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def _confidence(sample_count: int, min_sample_size: int) -> float:
    if min_sample_size <= 0:
        return 1.0
    return max(0.0, min(1.0, sample_count / float(min_sample_size)))


def _segment_breakdown(
    observations: list[OutcomeObservation],
    contract: OutcomeContract,
) -> list[dict[str, Any]]:
    if not contract.dimensions:
        return []
    grouped: dict[tuple[tuple[str, Any], ...], list[float]] = {}
    for observation in observations:
        value = observation.metrics.get(contract.primary_kpi)
        if value is None:
            continue
        segment = tuple(
            (name, observation.dimensions.get(name))
            for name in contract.dimensions
            if observation.dimensions.get(name) not in (None, "")
        )
        if not segment:
            continue
        try:
            grouped.setdefault(segment, []).append(float(value))
        except (TypeError, ValueError):
            continue
    segments = []
    for segment, values in grouped.items():
        segments.append(
            {
                "segment": {name: value for name, value in segment},
                "sample_count": len(values),
                "value": sum(values) / len(values),
            }
        )
    segments.sort(key=lambda item: item["value"])
    return segments[: contract.reference_update_policy.max_segments]


def _exemplar_slice(
    observations: list[OutcomeObservation],
    contract: OutcomeContract,
) -> list[dict[str, Any]]:
    exemplars = []
    for observation in observations:
        value = observation.metrics.get(contract.primary_kpi)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        exemplars.append(
            {
                "entity_id": observation.entity_id,
                "timestamp": observation.timestamp,
                contract.primary_kpi: numeric,
                "dimensions": dict(observation.dimensions),
                "source": observation.source,
                "skill_name": observation.skill_name,
                "skill_version": observation.skill_version,
                "exemplar": observation.metadata.get("exemplar", ""),
            }
        )
    exemplars.sort(key=lambda item: item[contract.primary_kpi])
    return exemplars[: contract.reference_update_policy.max_exemplars]


def _outcome_stats(
    observations: list[OutcomeObservation],
    contract: OutcomeContract,
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for metric in [contract.primary_kpi, *contract.supporting_kpis]:
        value = _average_metric(observations, metric)
        if value is None:
            continue
        stats[metric] = {"average": value}
    return stats


def analyze_outcomes(
    snapshot: MetricsSnapshot,
    contract: OutcomeContract,
    *,
    skill_name: str = "",
) -> OutcomeAnalysisResult:
    observations = _filter_observations(snapshot, skill_name=skill_name)
    if not observations:
        return OutcomeAnalysisResult(
            skill_name=skill_name,
            total_observations=0,
            summary=f"{skill_name}: no outcome observations available.",
        )

    ordered = _sort_outcomes(observations)
    if not ordered:
        return OutcomeAnalysisResult(
            skill_name=skill_name,
            total_observations=len(observations),
            summary=f"{skill_name}: outcome observations missing timestamps; governance skipped.",
            raw_stats=_outcome_stats(observations, contract),
            outcome_summary=OutcomeWindowSummary(
                window=contract.window.to_dict(),
                sample_count=0,
                baseline_value=None,
                current_value=None,
                delta=None,
                confidence=0.0,
                segment_breakdown=[],
                policy_state={
                    "missing_timestamp": True,
                    "insufficient_sample": True,
                    "rewrite_candidate": False,
                    "rollback_candidate": False,
                },
            ),
        )

    latest = ordered[0][0]
    current_cutoff = latest - timedelta(days=contract.window.current_days)
    baseline_cutoff = current_cutoff - timedelta(days=contract.window.baseline_days)

    current_window = [item for observed_at, item in ordered if observed_at >= current_cutoff]
    baseline_window = [
        item for observed_at, item in ordered if baseline_cutoff <= observed_at < current_cutoff
    ]
    current_value = _average_metric(current_window, contract.primary_kpi)
    baseline_value = _average_metric(baseline_window, contract.primary_kpi)
    delta = (
        None if current_value is None or baseline_value is None else current_value - baseline_value
    )
    sample_count = len(current_window)
    segment_breakdown = _segment_breakdown(current_window, contract)
    exemplar_slice = _exemplar_slice(current_window, contract)
    insufficient_sample = sample_count < contract.min_sample_size
    target_breached = (
        contract.rewrite_policy.target is not None
        and current_value is not None
        and current_value < contract.rewrite_policy.target
    )
    delta_breached = delta is not None and delta <= -contract.rewrite_policy.min_delta
    severe_regression = (
        delta is not None and delta <= -contract.rewrite_policy.severe_regression_delta
    )
    rewrite_candidate = not insufficient_sample and (target_breached or delta_breached)
    if contract.rewrite_policy.require_segment:
        rewrite_candidate = rewrite_candidate and bool(segment_breakdown or exemplar_slice)
    rollback_candidate = delta is not None and delta <= -contract.rollback_policy.max_negative_delta
    outcome_summary = OutcomeWindowSummary(
        window=contract.window.to_dict(),
        sample_count=sample_count,
        baseline_value=baseline_value,
        current_value=current_value,
        delta=delta,
        confidence=_confidence(sample_count, contract.min_sample_size),
        segment_breakdown=segment_breakdown,
        policy_state={
            "missing_timestamp": False,
            "insufficient_sample": insufficient_sample,
            "target_breached": target_breached,
            "delta_breached": delta_breached,
            "severe_regression": severe_regression,
            "rewrite_candidate": rewrite_candidate,
            "rollback_candidate": rollback_candidate,
        },
    )
    summary = (
        f"{skill_name}: {contract.primary_kpi} current={current_value:.3f} "
        f"baseline={baseline_value:.3f} delta={delta:.3f} samples={sample_count}"
        if current_value is not None and baseline_value is not None and delta is not None
        else f"{skill_name}: {contract.primary_kpi} samples={sample_count}, insufficient comparison."
    )
    return OutcomeAnalysisResult(
        skill_name=skill_name,
        total_observations=len(observations),
        summary=summary,
        outcome_summary=outcome_summary,
        regression_summary={
            "primary_kpi": contract.primary_kpi,
            "rewrite_candidate": rewrite_candidate,
            "rollback_candidate": rollback_candidate,
            "severe_regression": severe_regression,
        },
        exemplar_slice=exemplar_slice,
        raw_stats=_outcome_stats(observations, contract),
    )
