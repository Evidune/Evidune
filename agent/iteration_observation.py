"""Post-rewrite observation window verdicts for the iteration harness.

After the harness applies a rewrite, the skill enters an "observing" status
instead of going straight back to "active". On the next harness run this
module compares evaluator scores recorded after the rewrite against the
execution-contract thresholds and returns one of three verdicts:

- confirm:  enough post-rewrite samples and the average clears min_pass_score
- rollback: enough samples and the average is at or below rewrite_below_score
- hold:     not enough samples yet, or the average sits between the bounds
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from agent.iteration_harness import IterationDecisionPacket

OBSERVING_STATUS = "observing"

_DEFAULT_MIN_SAMPLES = 3
_DEFAULT_MIN_PASS_SCORE = 0.70
_DEFAULT_ROLLBACK_BELOW_SCORE = 0.55


@dataclass
class ObservationOutcome:
    verdict: str  # "hold" | "confirm" | "rollback"
    window: dict[str, Any]


def observation_outcome(
    packet: IterationDecisionPacket,
    rewrite_event: dict[str, Any],
) -> ObservationOutcome:
    """Judge the observation window opened by the given rewrite lifecycle event."""
    contract = packet.execution_contract or {}
    min_samples = int(contract.get("min_samples_for_rewrite", _DEFAULT_MIN_SAMPLES) or 0)
    min_pass = float(contract.get("min_pass_score", _DEFAULT_MIN_PASS_SCORE) or 0.0)
    rollback_below = float(
        contract.get("rewrite_below_score", _DEFAULT_ROLLBACK_BELOW_SCORE) or 0.0
    )

    rewritten_at = str(rewrite_event.get("created_at") or "")
    # Run-only deployments never record execution evaluations (the judge only
    # runs in serve), so resolve the window from post-rewrite KPI windows
    # instead of holding forever and freezing reference refreshes.
    if not packet.execution_evaluations:
        return _outcome_window_verdict(packet, rewritten_at, min_samples)
    scores = _post_rewrite_scores(packet.execution_evaluations, rewritten_at)
    average = (sum(scores) / len(scores)) if scores else None
    window = {
        "rewritten_at": rewritten_at,
        "samples": len(scores),
        "average_score": average,
        "min_samples": min_samples,
        "min_pass_score": min_pass,
        "rollback_below_score": rollback_below,
    }

    if average is None or len(scores) < min_samples:
        return ObservationOutcome(verdict="hold", window=window)
    if average >= min_pass:
        return ObservationOutcome(verdict="confirm", window=window)
    if average <= rollback_below:
        return ObservationOutcome(verdict="rollback", window=window)
    return ObservationOutcome(verdict="hold", window=window)


def _outcome_window_verdict(
    packet: IterationDecisionPacket,
    rewritten_at: str,
    min_samples: int,
) -> ObservationOutcome:
    """Resolve the window from outcome KPI windows recorded after the rewrite."""
    summaries = [
        item
        for item in packet.outcome_summaries
        if str(item.get("created_at") or "") > rewritten_at
    ]
    window = {
        "rewritten_at": rewritten_at,
        "samples": len(summaries),
        "average_score": None,
        "min_samples": min_samples,
        "source": "outcome_windows",
    }
    if len(summaries) < min_samples:
        return ObservationOutcome(verdict="hold", window=window)
    # Summaries are newest-first; the latest policy state carries the verdict.
    policy_state = summaries[0].get("policy_state") or {}
    if policy_state.get("rollback_candidate") or policy_state.get("severe_regression"):
        return ObservationOutcome(verdict="rollback", window=window)
    return ObservationOutcome(verdict="confirm", window=window)


def _post_rewrite_scores(
    evaluations: list[dict[str, Any]],
    rewritten_at: str,
) -> list[float]:
    """Aggregate scores recorded strictly after the rewrite (ISO timestamps)."""
    scores: list[float] = []
    for item in evaluations:
        if item.get("aggregate_score") is None:
            continue
        if str(item.get("created_at") or "") <= rewritten_at:
            continue
        try:
            scores.append(float(item["aggregate_score"]))
        except (TypeError, ValueError):
            continue
    return scores
