"""Summarise execution feedback into skill-level decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.signal_collector import aggregate, signals_from_dict

# Decision thresholds for combining signals and evaluator scores.
DISABLE_COMBINED_THRESHOLD = -0.35
DISABLE_STRONG_SIGNAL_CONFIDENCE = -0.5
DISABLE_AVERAGE_SCORE = 0.25
REWRITE_COMBINED_THRESHOLD = 0.2
REWRITE_STRONG_SIGNAL_CONFIDENCE = 0.5
REWRITE_AVERAGE_SCORE = 0.7

# Minimum total evidence (signal samples + score samples) before acting, plus
# a floor on distinct executions: samples within one execution are correlated
# (e.g. thumbs_down + regenerated on the same reply), so a single interaction
# must never disable or rewrite a skill on its own.
MIN_EVIDENCE_FOR_DISABLE = 3
MIN_EVIDENCE_FOR_REWRITE = 2
MIN_EXECUTIONS_FOR_ACTION = 2

# Exponential recency decay applied per execution: the most recent execution
# gets weight DECAY**0 = 1.0, the next DECAY**1, and so on.
DECAY = 0.85


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


def _weighted_mean(terms: list[tuple[float, float]]) -> float:
    """Mean of (weight, value) terms; caller guarantees terms is non-empty."""
    total_weight = sum(weight for weight, _ in terms)
    return sum(weight * value for weight, value in terms) / total_weight


def summarise_skill_feedback(executions: list[dict[str, Any]]) -> SkillFeedbackSummary:
    """Combine stored signals and evaluator scores into one decision summary.

    Executions must be ordered most-recent-first (the order returned by
    ``MemoryStore.get_skill_executions``). Each execution's signals are
    aggregated on their own, then per-execution confidences and evaluator
    scores are averaged with exponential recency decay (weight =
    DECAY**index) so recent evidence outweighs old evidence.
    """
    confidence_terms: list[tuple[float, float]] = []  # (decay weight, confidence)
    score_terms: list[tuple[float, float]] = []  # (decay weight, score)
    signal_samples = 0
    score_samples = 0
    evidenced_executions = 0
    has_strong_signal = False
    breakdown: dict[str, Any] = {}

    for index, execution in enumerate(executions):
        decay = DECAY**index
        contributed = False
        aggregated = aggregate(signals_from_dict(execution.get("signals") or {}))
        # Newest-wins merge: iteration is most-recent-first, so keep the first
        # value seen for each signal key in the recorded evidence.
        for key, value in aggregated.breakdown.items():
            breakdown.setdefault(key, value)
        if aggregated.sample_count > 0:
            confidence_terms.append((decay, aggregated.confidence))
            signal_samples += aggregated.sample_count
            has_strong_signal = has_strong_signal or aggregated.has_strong_signal
            contributed = True

        score = execution.get("score")
        if score is not None:
            try:
                score_terms.append((decay, float(score)))
                score_samples += 1
                contributed = True
            except (TypeError, ValueError):
                pass
        if contributed:
            evidenced_executions += 1

    signal_confidence = _weighted_mean(confidence_terms) if confidence_terms else 0.0
    average_score = _weighted_mean(score_terms) if score_terms else None
    score_confidence = _score_to_confidence(average_score)

    inputs: list[float] = []
    if signal_samples > 0:
        inputs.append(signal_confidence)
    if score_confidence is not None:
        inputs.append(score_confidence)

    combined = (sum(inputs) / len(inputs)) if inputs else 0.0
    total_samples = signal_samples + score_samples
    should_disable = (
        total_samples >= MIN_EVIDENCE_FOR_DISABLE
        and evidenced_executions >= MIN_EXECUTIONS_FOR_ACTION
        and (
            combined <= DISABLE_COMBINED_THRESHOLD
            or (has_strong_signal and signal_confidence <= DISABLE_STRONG_SIGNAL_CONFIDENCE)
            or (
                # A single low judge score must not trigger the score-only path.
                score_samples >= MIN_EXECUTIONS_FOR_ACTION
                and average_score is not None
                and average_score <= DISABLE_AVERAGE_SCORE
            )
        )
    )
    should_rewrite = (
        not should_disable
        and total_samples >= MIN_EVIDENCE_FOR_REWRITE
        and evidenced_executions >= MIN_EXECUTIONS_FOR_ACTION
        and (
            combined >= REWRITE_COMBINED_THRESHOLD
            or (has_strong_signal and signal_confidence >= REWRITE_STRONG_SIGNAL_CONFIDENCE)
            or (average_score is not None and average_score >= REWRITE_AVERAGE_SCORE)
        )
    )

    evidence = {
        "signal_confidence": signal_confidence,
        "signal_samples": signal_samples,
        "evidenced_executions": evidenced_executions,
        "has_strong_signal": has_strong_signal,
        "signal_breakdown": breakdown,
        "average_score": average_score,
        "score_samples": score_samples,
        "combined_confidence": combined,
    }

    return SkillFeedbackSummary(
        signal_confidence=signal_confidence,
        signal_samples=signal_samples,
        has_strong_signal=has_strong_signal,
        average_score=average_score,
        score_samples=score_samples,
        combined_confidence=combined,
        should_rewrite=should_rewrite,
        should_disable=should_disable,
        evidence=evidence,
    )
