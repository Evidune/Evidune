"""Analysis engine: pattern extraction, ranking, and trend detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.metrics import MetricRecord, MetricsSnapshot


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
            patterns.append(f"Top performers have longer titles (avg {avg_top_title:.0f} vs {avg_bottom_title:.0f} chars)")
        elif avg_bottom_title > avg_top_title * 1.3:
            patterns.append(f"Top performers have shorter titles (avg {avg_top_title:.0f} vs {avg_bottom_title:.0f} chars)")

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
        if top else f"{snapshot.domain}: {len(records)} items analyzed."
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
