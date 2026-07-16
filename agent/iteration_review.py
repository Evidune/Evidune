"""Deterministic content checks for iteration harness proposals.

The `safety_reviewer` role in the iteration harness delegates to
`review_proposal` so the recorded audit trail reflects real checks instead of
a change-detection stub. This module also owns the shared markdown-section
helpers used by the harness proposal builders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from agent.iteration_harness import IterationDecisionPacket

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
MANAGED_ADJUSTMENTS_HEADING = "### Outcome-Backed Adjustments"
INSTRUCTIONS_HEADING = "## Instructions"

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_MIN_SIZE_RATIO = 0.3
_MAX_SIZE_RATIO = 3.0
# Small skills legitimately grow past 3x when the first evidence sections are
# appended, so the upper bound also allows a fixed absolute headroom.
_GROWTH_HEADROOM_CHARS = 2000
_LEAKAGE_NGRAM_TOKENS = 8
_TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)


def extract_section(body: str, heading: str) -> str | None:
    """Return the stripped body of a markdown section, or None if absent."""
    target = heading.lstrip("#").strip()
    lines = body.split("\n")
    start_idx: int | None = None
    start_level: int | None = None

    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if start_idx is None and title == target:
            start_idx = idx
            start_level = level
        elif start_idx is not None and level <= (start_level or 0):
            return "\n".join(lines[start_idx + 1 : idx]).strip()

    if start_idx is not None:
        return "\n".join(lines[start_idx + 1 :]).strip()
    return None


def strip_managed_adjustments(instructions_body: str) -> str:
    """Drop the harness-managed adjustments subsection, keeping manual text."""
    lines = instructions_body.rstrip().split("\n")
    output: list[str] = []
    skipping = False

    for line in lines:
        if line.strip() == MANAGED_ADJUSTMENTS_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            output.append(line)

    return "\n".join(output).rstrip()


def split_frontmatter(content: str) -> tuple[str, str]:
    """Split content into its raw YAML frontmatter block and body."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return "", content
    return match.group(0), content[match.end() :].lstrip("\n")


@dataclass
class ReviewResult:
    approved: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def review_proposal(
    decision: str,
    packet: IterationDecisionPacket,
    proposed_content: str,
) -> ReviewResult:
    """Review a rewrite/refresh/rollback proposal against the current content.

    keep/disable carry no proposal, so they pass with no checks recorded.
    Every executed check lands in `checks`; failures add a human-readable
    reason so the audit trail states exactly why a proposal was rejected.
    """
    if decision not in {"rewrite", "refresh", "rollback"}:
        return ReviewResult(approved=True)

    checks: dict[str, bool] = {}
    reasons: list[str] = []
    current = packet.current_content or ""

    checks["non_empty"] = bool(proposed_content.strip())
    if not checks["non_empty"]:
        reasons.append("proposal content is empty")
        return ReviewResult(approved=False, checks=checks, reasons=reasons)

    current_frontmatter, current_body = split_frontmatter(current)
    proposed_frontmatter, proposed_body = split_frontmatter(proposed_content)
    if current_frontmatter:
        # The regex prefix absorbs blank lines after the closing ---, so
        # compare with boundary whitespace normalized: only YAML changes count.
        checks["frontmatter_preserved"] = (
            proposed_frontmatter.rstrip() == current_frontmatter.rstrip()
        )
        if not checks["frontmatter_preserved"]:
            reasons.append("YAML frontmatter was altered or dropped")

    current_instructions = extract_section(current_body, INSTRUCTIONS_HEADING)
    proposed_instructions = extract_section(proposed_body, INSTRUCTIONS_HEADING)
    if current_instructions is not None:
        checks["instructions_present"] = proposed_instructions is not None
        if not checks["instructions_present"]:
            reasons.append("'## Instructions' section was removed")

    if decision == "rewrite" and current_instructions:
        base = strip_managed_adjustments(current_instructions)
        if base:
            checks["manual_instructions_preserved"] = base in (proposed_instructions or "")
            if not checks["manual_instructions_preserved"]:
                reasons.append("manual instructions were deleted or altered")

    if decision in {"rewrite", "refresh"} and packet.executions:
        checks["source_task_not_copied"] = not _copies_new_source_task_ngram(
            current,
            proposed_content,
            packet.executions,
        )
        if not checks["source_task_not_copied"]:
            reasons.append("proposal copies a source-task phrase into the Skill")

    if decision == "rewrite" and packet.experiment_history:
        rejected_bodies = {
            split_frontmatter(str(experiment.get("candidate_content") or ""))[1].strip()
            for experiment in packet.experiment_history
            if experiment.get("status") == "rejected"
        }
        checks["not_duplicate_rejected_candidate"] = proposed_body.strip() not in rejected_bodies
        if not checks["not_duplicate_rejected_candidate"]:
            reasons.append("proposal duplicates a previously rejected candidate")

    size_reference = current
    if decision == "rollback":
        content_before = _latest_rewrite_content_before(packet.lifecycle_history)
        restored_instructions = (
            extract_section(split_frontmatter(content_before)[1], INSTRUCTIONS_HEADING)
            if content_before
            else None
        )
        checks["rollback_matches_history"] = bool(content_before) and (
            proposed_instructions == restored_instructions
        )
        if not checks["rollback_matches_history"]:
            reasons.append("rollback does not restore the recorded pre-rewrite instructions")
        if content_before:
            size_reference = content_before

    checks["size_sane"] = _size_is_sane(len(size_reference), len(proposed_content))
    if not checks["size_sane"]:
        reasons.append(
            f"proposal size {len(proposed_content)} chars is outside sane bounds "
            f"for reference size {len(size_reference)} chars"
        )

    return ReviewResult(approved=all(checks.values()), checks=checks, reasons=reasons)


def _size_is_sane(reference_length: int, proposed_length: int) -> bool:
    """Guard against truncated proposals and runaway content growth."""
    if reference_length <= 0:
        return proposed_length > 0
    lower = int(reference_length * _MIN_SIZE_RATIO)
    upper = max(
        int(reference_length * _MAX_SIZE_RATIO),
        reference_length + _GROWTH_HEADROOM_CHARS,
    )
    return lower <= proposed_length <= upper


def _normalized_tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(value)]


def _copies_new_source_task_ngram(
    current: str,
    proposed: str,
    executions: list[dict[str, Any]],
) -> bool:
    current_text = " ".join(_normalized_tokens(current))
    proposed_text = " ".join(_normalized_tokens(proposed))
    for execution in executions:
        tokens = _normalized_tokens(str(execution.get("user_input") or ""))
        for index in range(len(tokens) - _LEAKAGE_NGRAM_TOKENS + 1):
            phrase = " ".join(tokens[index : index + _LEAKAGE_NGRAM_TOKENS])
            if phrase not in current_text and phrase in proposed_text:
                return True
    return False


def _latest_rewrite_content_before(history: list[dict[str, Any]]) -> str:
    """Mirror the harness rollback source: the latest recorded rewrite event."""
    for event in history:
        if event.get("action") == "rewrite":
            return str(event.get("content_before") or "")
    return ""
