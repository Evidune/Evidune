"""Helpers used by the iteration loop (core/loop.py).

Kept separate so `loop.py` stays focused on orchestration — the
content-building functions below are pure and trivially testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.analyzer import AnalysisResult
from core.config import AiflayConfig
from core.updater import UpdateResult, update_reference


def update_outcome_skills(
    config: AiflayConfig,
    base_dir: Path,
    result: AnalysisResult,
) -> list[UpdateResult]:
    """Update SKILL.md files that opt into outcome-driven iteration.

    For each skill with `outcome_metrics: true` in its frontmatter,
    replaces the skill's `update_section` (default "## Reference Data")
    with fresh Top Performers + Patterns derived from the analysis.
    """
    from skills.registry import SkillRegistry

    registry = SkillRegistry()
    for skill_dir in config.skills.directories:
        registry.load_directory(base_dir / skill_dir)

    skill_updates: list[UpdateResult] = []
    for skill in registry.get_outcome_skills():
        section = skill.update_section
        new_content = build_skill_reference_content(section, result)
        update = update_reference(
            path=skill.path,
            strategy="replace_section",
            new_content=new_content,
            section=section,
        )
        skill_updates.append(update)
    return skill_updates


def build_skill_reference_content(section: str, result: AnalysisResult) -> str:
    """Build the Reference Data section for an outcome-metrics skill.

    Output: heading + timestamp + Top Performers + Patterns.
    Bottom performers are omitted (not useful as positive guidance).
    """
    lines = [section, ""]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Auto-updated by aiflay on {timestamp}*")
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
    references listed in the `references:` section of aiflay.yaml —
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
