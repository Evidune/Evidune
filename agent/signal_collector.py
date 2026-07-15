"""Signal aggregation for skill executions.

Turns the signals observed for a single execution (explicit ratings, thumbs,
implicit behaviour) into one confidence score for that execution.

Signal types and weights:
  thumbs_up     +1.0   (strong positive)
  thumbs_down   -1.0   (strong negative)
  copied        +0.5   (positive — user took the output)
  regenerated   -0.7   (negative — user wanted a different answer)
  edited_request -0.5  (negative — next user msg was a correction)
  topic_switch   0.0   (neutral — user moved on, ambiguous)
  silent         0.0   (neutral — no follow-up within timeout)
  rating_int     -1..+1 (numeric explicit rating, normalised)

Aggregation is precedence-based, not additive. The strongest tier of
evidence present decides the confidence:

  1. rating signals  -> confidence = mean of normalised ratings
  2. thumbs signals  -> confidence = mean of thumbs weights
  3. weak signals    -> confidence = mean of non-neutral weights, clamped

Lower-tier signals never dilute a stronger tier (a pile of "copied" events
cannot outweigh a thumbs_down), but every observed signal still appears in
the breakdown and every non-neutral one counts toward sample_count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Signal weights — tune as we learn what works
_SIGNAL_WEIGHTS = {
    "thumbs_up": 1.0,
    "thumbs_down": -1.0,
    "copied": 0.5,
    "regenerated": -0.7,
    "edited_request": -0.5,
    "topic_switch": 0.0,
    "silent": 0.0,
}

_STRONG_SIGNALS = {"thumbs_up", "thumbs_down", "rating"}


@dataclass
class Signal:
    """A single observed signal for a skill execution."""

    type: str  # one of the keys in _SIGNAL_WEIGHTS or "rating"
    value: Any = True  # bool for flags, int for ratings


@dataclass
class AggregatedSignal:
    """Result of aggregating multiple signals."""

    confidence: float  # -1.0 (strongly negative) to +1.0 (strongly positive)
    sample_count: int
    has_strong_signal: bool  # whether any thumbs/rating was provided
    breakdown: dict[str, Any] = field(default_factory=dict)


def _normalise_rating(value: Any) -> float:
    """Normalise an explicit rating to [-1, 1].

    Accepts int 1-5 (1=worst, 5=best) or 0-100 percentage.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0

    if 0 <= v <= 5:
        # 1-5 scale (3 = neutral)
        return (v - 3) / 2  # 1→-1, 5→+1
    if 0 <= v <= 100:
        return (v - 50) / 50  # 0→-1, 100→+1
    return max(-1.0, min(1.0, v))


def aggregate(signals: list[Signal]) -> AggregatedSignal:
    """Aggregate one execution's signals into a confidence score in [-1, 1].

    The strongest evidence tier present wins outright:
    - any 'rating' signals: confidence = mean of their normalised values;
    - else any thumbs signals: confidence = mean of their weights;
    - else: confidence = mean of non-neutral weak-signal weights.

    sample_count counts all non-neutral signals (including those in tiers
    that did not decide the confidence); the breakdown records every
    observed signal.
    """
    if not signals:
        return AggregatedSignal(confidence=0.0, sample_count=0, has_strong_signal=False)

    breakdown: dict[str, Any] = {}
    ratings: list[float] = []
    thumbs: list[float] = []
    weak: list[float] = []
    non_neutral = 0

    for sig in signals:
        if sig.type == "rating":
            breakdown["rating"] = sig.value
            ratings.append(_normalise_rating(sig.value))
            non_neutral += 1
            continue

        if sig.value is False:
            # An explicit False (e.g. "did not copy") — skip
            continue

        weight = _SIGNAL_WEIGHTS.get(sig.type, 0.0)
        breakdown[sig.type] = sig.value
        if sig.type in _STRONG_SIGNALS:
            thumbs.append(weight)
            non_neutral += 1
        elif weight != 0.0:
            weak.append(weight)
            non_neutral += 1

    decisive = ratings or thumbs or weak
    confidence = (sum(decisive) / len(decisive)) if decisive else 0.0
    confidence = max(-1.0, min(1.0, confidence))
    return AggregatedSignal(
        confidence=confidence,
        sample_count=non_neutral,
        has_strong_signal=bool(ratings or thumbs),
        breakdown=breakdown,
    )


def signals_from_dict(d: dict[str, Any]) -> list[Signal]:
    """Convert a stored signals_json dict into a list of Signal objects."""
    return [Signal(type=k, value=v) for k, v in d.items()]
