"""Helpers for persisting and inspecting outcome iteration runs."""

from __future__ import annotations

from typing import Any

from channels.base import IterationReport
from core.config import EviduneConfig
from core.metrics import MetricsSnapshot
from memory.store import MemoryStore


def _metric_record_to_dict(record) -> dict[str, Any]:
    return {
        "title": record.title,
        "metrics": dict(record.metrics),
        "metadata": dict(record.metadata),
    }


def _metrics_source(config: EviduneConfig) -> str:
    source = config.metrics.config.get("file", "")
    return source if isinstance(source, str) else ""


def record_iteration_report(
    memory: MemoryStore,
    config: EviduneConfig,
    snapshot: MetricsSnapshot,
    report: IterationReport,
    sort_metric: str,
) -> int:
    """Persist one completed iteration report to the memory ledger."""
    return memory.record_iteration_run(
        domain=report.domain,
        metrics_adapter=config.metrics.adapter,
        metrics_source=_metrics_source(config),
        sort_metric=sort_metric,
        total_records=report.analysis.total_records,
        summary=report.analysis.summary,
        patterns=report.analysis.patterns,
        raw_stats=report.analysis.raw_stats,
        top_performers=[_metric_record_to_dict(item) for item in report.analysis.top_performers],
        bottom_performers=[
            _metric_record_to_dict(item) for item in report.analysis.bottom_performers
        ],
        updates=[
            {
                "path": item.path,
                "strategy": item.strategy,
                "has_changes": item.has_changes,
            }
            for item in report.updates
        ],
        commit_sha=report.commit_sha,
    )


def format_iteration_runs(runs: list[dict[str, Any]]) -> str:
    """Render a compact list of iteration runs for the CLI."""
    if not runs:
        return "No iteration runs recorded."

    lines: list[str] = []
    for run in runs:
        head = f"{run['id']}. [{run['domain']}] {run['summary']}"
        tail = (
            f"  adapter={run['metrics_adapter']}"
            f" records={run['total_records']}"
            f" changed={run.get('changed_count', 0)}/{run.get('update_count', 0)}"
        )
        if run.get("commit_sha"):
            tail += f" commit={run['commit_sha'][:8]}"
        lines.extend([head, tail])
    return "\n".join(lines)


def format_iteration_run(run: dict[str, Any] | None) -> str:
    """Render one detailed iteration run for the CLI."""
    if not run:
        return "Iteration run not found."

    lines = [
        f"Iteration Run #{run['id']}",
        f"Domain: {run['domain']}",
        f"Metrics Adapter: {run['metrics_adapter']}",
        f"Metrics Source: {run['metrics_source'] or '(none)'}",
        f"Sort Metric: {run['sort_metric'] or '(default)'}",
        f"Total Records: {run['total_records']}",
        f"Created At: {run['created_at']}",
        f"Commit: {run['commit_sha'] or '(none)'}",
        "",
        "Summary:",
        run["summary"],
    ]

    patterns = run.get("patterns") or []
    lines.extend(["", "Patterns:"])
    if patterns:
        lines.extend(f"- {pattern}" for pattern in patterns)
    else:
        lines.append("(none)")

    updates = run.get("updates") or []
    lines.extend(["", "Updates:"])
    if updates:
        for update in updates:
            marker = "changed" if update["has_changes"] else "unchanged"
            lines.append(f"- {update['path']} [{update['strategy']}] {marker}")
    else:
        lines.append("(none)")

    return "\n".join(lines)
