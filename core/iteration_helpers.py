"""Helpers used by the iteration loop (core/loop.py).

Kept separate so `loop.py` stays focused on orchestration — the
content-building functions below are pure and trivially testable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agent.skill_feedback import SkillFeedbackSummary, summarise_skill_feedback
from core.analyzer import AnalysisResult
from core.config import EviduneConfig
from core.updater import UpdateResult, replace_section, update_reference

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_MANAGED_ADJUSTMENTS_HEADING = "### Outcome-Backed Adjustments"


def update_outcome_skills(
    config: EviduneConfig,
    base_dir: Path,
    result: AnalysisResult,
    memory,
) -> list[UpdateResult]:
    """Update SKILL.md files that opt into outcome-driven iteration.

    For each skill with `outcome_metrics: true` in its frontmatter,
    refreshes evidence and, when guardrails allow, rewrites the
    `## Instructions` section with outcome-backed adjustments.
    """
    from skills.registry import SkillRegistry

    registry = SkillRegistry()
    for skill_dir in config.skills.directories:
        registry.load_directory(base_dir / skill_dir)

    skill_updates: list[UpdateResult] = []
    workflow_enabled = True
    if config.agent is not None:
        workflow_enabled = getattr(config.agent.harness, "iteration_workflow_enabled", True)
    for skill in registry.get_outcome_skills():
        origin = "emerged" if memory.get_emerged_skill(skill.name) else "base"
        memory.upsert_skill_state(
            skill.name,
            origin=origin,
            path=str(skill.path),
            status=memory.resolve_skill_status(skill.name),
        )
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
        if workflow_enabled:
            update = _update_outcome_skill(skill, result, feedback, memory)
        else:
            reference_content = build_skill_reference_content(skill.update_section, result)
            update = update_reference(
                path=skill.path,
                strategy="replace_section",
                new_content=reference_content,
                section=skill.update_section,
            )
        skill_updates.append(update)
    return skill_updates


def _update_outcome_skill(skill, result, feedback: SkillFeedbackSummary, memory) -> UpdateResult:
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


def _rewrite_skill(
    skill,
    current: str,
    result: AnalysisResult,
    feedback: SkillFeedbackSummary,
    memory,
) -> UpdateResult | None:
    prefix, body = _split_frontmatter(current)
    instructions_body = _extract_section(body, "## Instructions")
    if instructions_body is None:
        return None

    reference_content = build_skill_reference_content(skill.update_section, result)
    rewritten_instructions = _build_rewritten_instructions(instructions_body, result, feedback)
    new_body = replace_section(body, "## Instructions", rewritten_instructions)
    new_body = replace_section(new_body, skill.update_section, reference_content)
    new_content = (prefix + new_body.rstrip() + "\n") if prefix else (new_body.rstrip() + "\n")
    if new_content == current:
        return None

    skill.path.write_text(new_content, encoding="utf-8")
    evidence = dict(feedback.evidence)
    evidence["top_titles"] = [record.title for record in result.top_performers]
    evidence["patterns"] = list(result.patterns)
    memory.record_skill_lifecycle_event(
        skill.name,
        "rewrite",
        path=str(skill.path),
        reason="Outcome-driven rewrite from metrics and execution evidence",
        evidence=evidence,
        content_before=current,
        content_after=new_content,
    )
    return UpdateResult(
        path=str(skill.path),
        strategy="skill_rewrite",
        has_changes=True,
        old_content=current,
        new_content=new_content,
    )


def _rollback_skill(
    skill,
    current: str,
    result: AnalysisResult,
    feedback: SkillFeedbackSummary,
    memory,
) -> UpdateResult | None:
    event = memory.get_latest_skill_lifecycle_event(skill.name, action="rewrite")
    if not event or not event.get("content_before"):
        return None

    restored = event["content_before"]
    prefix, body = _split_frontmatter(restored)
    updated_body = replace_section(
        body,
        skill.update_section,
        build_skill_reference_content(skill.update_section, result),
    )
    new_content = (
        (prefix + updated_body.rstrip() + "\n") if prefix else (updated_body.rstrip() + "\n")
    )
    if new_content == current:
        return None

    skill.path.write_text(new_content, encoding="utf-8")
    memory.record_skill_lifecycle_event(
        skill.name,
        "rollback",
        status="rolled_back",
        path=str(skill.path),
        reason="Negative feedback or evaluator score reverted the last automatic rewrite",
        evidence=feedback.evidence,
        content_before=current,
        content_after=new_content,
    )
    return UpdateResult(
        path=str(skill.path),
        strategy="skill_rollback",
        has_changes=True,
        old_content=current,
        new_content=new_content,
    )


def _build_rewritten_instructions(
    instructions_body: str,
    result: AnalysisResult,
    feedback: SkillFeedbackSummary,
) -> str:
    base = _strip_managed_adjustments(instructions_body).rstrip()
    lines = ["## Instructions", ""]
    if base:
        lines.append(base)
        lines.append("")

    lines.append(_MANAGED_ADJUSTMENTS_HEADING)
    lines.append("")
    if result.top_performers:
        exemplar_titles = ", ".join(record.title for record in result.top_performers[:2])
        lines.append(
            f"- Mirror the specificity and framing seen in top performers such as: {exemplar_titles}."
        )
    for pattern in result.patterns:
        lines.append(f"- Reinforce this observed pattern: {pattern}.")
    lines.append(
        "- Keep the manual guidance above intact; treat these adjustments as evidence-backed overrides only."
    )
    if feedback.average_score is not None:
        lines.append(
            f"- Recent evaluator average is {feedback.average_score:.2f}; keep iterating only while evidence stays positive."
        )
    return "\n".join(lines)


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


def build_skill_reference_content(section: str, result: AnalysisResult) -> str:
    """Build the Reference Data section for an outcome-metrics skill.

    Output: heading + timestamp + Top Performers + Patterns.
    Bottom performers are omitted (not useful as positive guidance).
    """
    lines = [section, ""]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Auto-updated by evidune on {timestamp}*")
    lines.append("")

    if result.top_performers:
        lines.append("### Top Performers")
        for i, r in enumerate(result.top_performers, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"{i}. **{r.title}** — {metrics_str}")
        lines.append("")

    if result.patterns:
        lines.append("### Patterns")
        for p in result.patterns:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


def build_reference_content(
    strategy: str,
    section: str | None,
    result: AnalysisResult,
) -> str:
    """Build new content for a standalone reference document.

    Includes Top Performers, Bottom Performers, and Patterns. Used for
    references listed in the `references:` section of evidune.yaml —
    *not* for outcome-metrics skills (those use
    `build_skill_reference_content` which omits bottoms).
    """
    lines: list[str] = []

    if strategy == "replace_section" and section:
        lines.append(section)
        lines.append("")

    if result.top_performers:
        lines.append("### Top Performers")
        for i, r in enumerate(result.top_performers, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"{i}. **{r.title}** — {metrics_str}")
        lines.append("")

    if result.bottom_performers:
        lines.append("### Bottom Performers")
        for r in result.bottom_performers:
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"- {r.title} — {metrics_str}")
        lines.append("")

    if result.patterns:
        lines.append("### Patterns")
        for p in result.patterns:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)
