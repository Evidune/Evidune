"""Summarise execution feedback into skill-level decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.signal_collector import aggregate, signals_from_dict


@dataclass
class SkillFeedbackSummary:
    """Collapsed view of recent execution evidence for one skill."""

    signal_confidence: float
    signal_samples: int
    has_strong_signal: bool
    average_score: float | None
    score_samples: int
    combined_confidence: float
    should_rewrite: bool
    should_disable: bool
    evidence: dict[str, Any] = field(default_factory=dict)


def _score_to_confidence(score: float | None) -> float | None:
    if score is None:
        return None
    return max(-1.0, min(1.0, (float(score) * 2.0) - 1.0))


def summarise_skill_feedback(executions: list[dict[str, Any]]) -> SkillFeedbackSummary:
    """Combine stored signals and evaluator scores into one decision summary."""
    all_signals = []
    scores: list[float] = []

    for execution in executions:
        all_signals.extend(signals_from_dict(execution.get("signals") or {}))
        score = execution.get("score")
        if score is not None:
            try:
                scores.append(float(score))
            except (TypeError, ValueError):
                continue

    aggregated = aggregate(all_signals)
    average_score = (sum(scores) / len(scores)) if scores else None
    score_confidence = _score_to_confidence(average_score)

    inputs: list[float] = []
    if aggregated.sample_count > 0:
        inputs.append(aggregated.confidence)
    if score_confidence is not None:
        inputs.append(score_confidence)

    combined = (sum(inputs) / len(inputs)) if inputs else 0.0
    should_disable = (
        combined <= -0.35
        or (aggregated.has_strong_signal and aggregated.confidence <= -0.5)
        or (average_score is not None and average_score <= 0.25)
    )
    should_rewrite = not should_disable and (
        combined >= 0.2
        or (aggregated.has_strong_signal and aggregated.confidence >= 0.5)
        or (average_score is not None and average_score >= 0.7)
        or not inputs
    )

    evidence = {
        "signal_confidence": aggregated.confidence,
        "signal_samples": aggregated.sample_count,
        "has_strong_signal": aggregated.has_strong_signal,
        "signal_breakdown": aggregated.breakdown,
        "average_score": average_score,
        "score_samples": len(scores),
        "combined_confidence": combined,
    }

    return SkillFeedbackSummary(
        signal_confidence=aggregated.confidence,
        signal_samples=aggregated.sample_count,
        has_strong_signal=aggregated.has_strong_signal,
        average_score=average_score,
        score_samples=len(scores),
        combined_confidence=combined,
        should_rewrite=should_rewrite,
        should_disable=should_disable,
        evidence=evidence,
    )
