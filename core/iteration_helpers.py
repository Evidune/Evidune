"""Helpers used by the iteration loop (core/loop.py)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agent.skill_feedback import SkillFeedbackSummary, summarise_skill_feedback
from core.analyzer import AnalysisResult, OutcomeAnalysisResult, analyze_outcomes
from core.config import EviduneConfig
from core.metrics import MetricsSnapshot
from core.updater import UpdateResult, update_reference

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_MANAGED_ADJUSTMENTS_HEADING = "### Outcome-Backed Adjustments"


def update_outcome_skills(
    config: EviduneConfig,
    base_dir: Path,
    snapshot: MetricsSnapshot,
    memory,
) -> list[UpdateResult]:
    """Update SKILL.md files that opt into explicit outcome governance."""
    from skills.registry import SkillRegistry

    registry = SkillRegistry()
    for skill_dir in config.skills.directories:
        registry.load_directory(base_dir / skill_dir)

    skill_updates: list[UpdateResult] = []
    workflow_enabled = True
    if config.agent is not None:
        workflow_enabled = getattr(config.agent.harness, "iteration_workflow_enabled", True)

    for skill in registry.all():
        origin = "emerged" if memory.get_emerged_skill(skill.name) else "base"
        memory.upsert_skill_state(
            skill.name,
            origin=origin,
            path=str(skill.path),
            status=memory.resolve_skill_status(skill.name),
        )
        if skill.outcome_contract is None:
            if skill.outcome_metrics:
                skill_updates.append(
                    UpdateResult(
                        path=str(skill.path),
                        strategy="skill_skipped_deprecated",
                        has_changes=False,
                        old_content="",
                        new_content="",
                    )
                )
            continue
        if memory.resolve_skill_status(skill.name) != "active":
            skill_updates.append(
                UpdateResult(
                    path=str(skill.path),
                    strategy="skill_skipped",
                    has_changes=False,
                    old_content="",
                    new_content="",
                )
            )
            continue

        feedback = summarise_skill_feedback(memory.get_skill_executions(skill.name, limit=20))
        outcome_result = analyze_outcomes(snapshot, skill.outcome_contract, skill_name=skill.name)
        observations = [
            item
            for item in snapshot.observations
            if not item.skill_name or item.skill_name == skill.name
        ]
        if observations:
            memory.record_outcome_observations(skill.name, observations)
        if outcome_result.outcome_summary is not None:
            memory.record_outcome_window_summary(
                skill_name=skill.name,
                primary_kpi=skill.outcome_contract.primary_kpi,
                summary={
                    "window": outcome_result.outcome_summary.window,
                    "sample_count": outcome_result.outcome_summary.sample_count,
                    "baseline_value": outcome_result.outcome_summary.baseline_value,
                    "current_value": outcome_result.outcome_summary.current_value,
                    "delta": outcome_result.outcome_summary.delta,
                    "confidence": outcome_result.outcome_summary.confidence,
                    "segment_breakdown": outcome_result.outcome_summary.segment_breakdown,
                    "policy_state": outcome_result.outcome_summary.policy_state,
                },
                raw_stats=outcome_result.raw_stats,
                exemplar_slice=outcome_result.exemplar_slice,
            )
        if workflow_enabled:
            update = _update_outcome_skill(skill, outcome_result, feedback, memory)
        else:
            reference_content = build_skill_reference_content(
                skill.update_section,
                outcome_result,
                primary_kpi=skill.outcome_contract.primary_kpi,
            )
            update = update_reference(
                path=skill.path,
                strategy="replace_section",
                new_content=reference_content,
                section=skill.update_section,
            )
        skill_updates.append(update)
    return skill_updates


def _update_outcome_skill(
    skill,
    result: OutcomeAnalysisResult,
    feedback: SkillFeedbackSummary,
    memory,
) -> UpdateResult:
    from core.iteration_harness import IterationHarness, build_decision_packet

    current = skill.path.read_text(encoding="utf-8")
    workflow = IterationHarness(memory)
    decision = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=current,
            feedback=feedback,
            result=result,
            surface="run",
            task_kind="skill_iteration",
        ),
    )
    return decision.update


def _strip_managed_adjustments(instructions_body: str) -> str:
    lines = instructions_body.rstrip().split("\n")
    output: list[str] = []
    skipping = False

    for line in lines:
        if line.strip() == _MANAGED_ADJUSTMENTS_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            output.append(line)

    return "\n".join(output).rstrip()


def _split_frontmatter(content: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return "", content
    return match.group(0), content[match.end() :].lstrip("\n")


def _extract_section(body: str, heading: str) -> str | None:
    target = heading.lstrip("#").strip()
    lines = body.split("\n")
    start_idx: int | None = None
    start_level: int | None = None

    for i, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if start_idx is None and title == target:
            start_idx = i
            start_level = level
        elif start_idx is not None and level <= (start_level or 0):
            return "\n".join(lines[start_idx + 1 : i]).strip()

    if start_idx is not None:
        return "\n".join(lines[start_idx + 1 :]).strip()
    return None


def build_skill_reference_content(
    section: str,
    result: OutcomeAnalysisResult,
    *,
    primary_kpi: str,
) -> str:
    """Build the managed reference section for an outcome-governed skill."""
    lines = [section, ""]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Auto-updated by evidune on {timestamp}*")
    lines.append("")
    lines.append(f"### KPI Window: {primary_kpi}")
    summary = result.outcome_summary
    if summary is None:
        lines.append(f"- Status: {result.summary}")
        return "\n".join(lines) + "\n"

    lines.append(f"- Sample size: {summary.sample_count}")
    if summary.current_value is not None:
        lines.append(f"- Current value: {summary.current_value:.3f}")
    if summary.baseline_value is not None:
        lines.append(f"- Baseline value: {summary.baseline_value:.3f}")
    if summary.delta is not None:
        lines.append(f"- Delta: {summary.delta:.3f}")
    lines.append(f"- Confidence: {summary.confidence:.2f}")
    if summary.policy_state:
        active = [key for key, value in summary.policy_state.items() if value is True]
        lines.append(f"- Policy state: {', '.join(active) if active else 'stable'}")
    lines.append("")

    if summary.segment_breakdown:
        lines.append("### Segment Breakdown")
        for segment in summary.segment_breakdown:
            label = ", ".join(f"{k}={v}" for k, v in segment.get("segment", {}).items())
            lines.append(
                f"- {label or '(unlabeled)'}: value={segment['value']:.3f}, "
                f"samples={segment['sample_count']}"
            )
        lines.append("")

    if result.exemplar_slice:
        lines.append("### Exemplars")
        for exemplar in result.exemplar_slice:
            label = exemplar.get("exemplar") or exemplar["entity_id"]
            lines.append(f"- {label} ({primary_kpi}={exemplar[primary_kpi]:.3f})")
        lines.append("")

    return "\n".join(lines)


def build_reference_content(
    strategy: str,
    section: str | None,
    result: AnalysisResult,
) -> str:
    """Build new content for a standalone reference document."""
    lines: list[str] = []

    if strategy == "replace_section" and section:
        lines.append(section)
        lines.append("")

    if result.top_performers:
        lines.append("### Top Performers")
        for i, record in enumerate(result.top_performers, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in record.metrics.items())
            lines.append(f"{i}. **{record.title}** — {metrics_str}")
        lines.append("")

    if result.bottom_performers:
        lines.append("### Bottom Performers")
        for record in result.bottom_performers:
            metrics_str = ", ".join(f"{k}={v}" for k, v in record.metrics.items())
            lines.append(f"- {record.title} — {metrics_str}")
        lines.append("")

    if result.patterns:
        lines.append("### Patterns")
        for pattern in result.patterns:
            lines.append(f"- {pattern}")
        lines.append("")

    return "\n".join(lines)
