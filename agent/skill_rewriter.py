"""LLM-backed SKILL.md rewrite proposals for the iteration harness.

The iteration harness stays deterministic and synchronous. Async surfaces
that have an LLM client call `propose_skill_rewrite` and pass the result
through `IterationDecisionPacket.llm_rewrite_proposal`; the harness prefers
it over the deterministic template only when it passes the same review gate.
Any LLM, parse, or review failure returns an empty string so the template
rewrite remains the fallback.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.iteration_review import review_proposal
from agent.utils import strip_code_fence

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from agent.iteration_harness import IterationDecisionPacket
    from agent.llm import LLMClient

logger = logging.getLogger("evidune.skill_rewriter")

_PROMPT_TEMPLATE = """You are the skill editor for an outcome-driven agent framework.
Revise the SKILL.md below using the evidence, then return the COMPLETE revised file.

# Current SKILL.md

{current}

# Evidence

{evidence}

# Output spec

Return ONLY the full revised SKILL.md content. No surrounding prose or code fences.
Hard requirements:
- Keep the YAML frontmatter byte-identical to the current file.
- Keep every existing section; only improve the '## Instructions' section.
- Preserve the manual guidance inside '## Instructions' verbatim, then add or
  replace a '### Outcome-Backed Adjustments' subsection with concrete,
  evidence-backed guidance derived from the evidence above.
- Do not invent metrics or facts that are not present in the evidence.
"""


async def propose_skill_rewrite(
    llm: LLMClient | None,
    packet: IterationDecisionPacket,
    **llm_kwargs: Any,
) -> str:
    """Generate a full revised SKILL.md, or "" so the template fallback applies."""
    if llm is None or not (packet.current_content or "").strip():
        return ""
    prompt = _PROMPT_TEMPLATE.format(
        current=packet.current_content,
        evidence=_evidence_block(packet),
    )
    kwargs = {"temperature": 0.2, **llm_kwargs}
    try:
        raw = await llm.complete([{"role": "user", "content": prompt}], **kwargs)
    except Exception:
        logger.warning("Skill rewrite proposal failed for %s", packet.skill_name, exc_info=True)
        return ""

    content = strip_code_fence(raw or "").strip()
    if not content:
        return ""
    content += "\n"
    review = review_proposal("rewrite", packet, content)
    if not review.approved:
        logger.info(
            "Discarding LLM rewrite proposal for %s: %s",
            packet.skill_name,
            "; ".join(review.reasons),
        )
        return ""
    return content


def _evidence_block(packet: IterationDecisionPacket) -> str:
    """Render the decision packet evidence as compact prompt bullets."""
    lines: list[str] = []
    primary_kpi = (packet.outcome_contract or {}).get("primary_kpi") or "primary KPI"

    summary = packet.outcome_summary or {}
    if summary:
        lines.append(
            f"- Outcome window for `{primary_kpi}`: current={summary.get('current_value')}, "
            f"baseline={summary.get('baseline_value')}, delta={summary.get('delta')}, "
            f"samples={summary.get('sample_count')}"
        )
    for segment in summary.get("segment_breakdown", [])[:3]:
        label = ", ".join(f"{k}={v}" for k, v in segment.get("segment", {}).items())
        lines.append(
            f"- Segment `{label or 'default'}`: value={segment.get('value')}, "
            f"samples={segment.get('sample_count')}"
        )
    for pattern in packet.regression_summary.get("legacy_patterns", [])[:3]:
        lines.append(f"- Observed pattern: {pattern}")
    for exemplar in packet.exemplar_slice[:3]:
        label = exemplar.get("exemplar") or exemplar.get("entity_id") or "example"
        value = exemplar.get(primary_kpi)
        lines.append(f"- Exemplar `{label}`" + (f" ({primary_kpi}={value})" if value else ""))

    if packet.feedback is not None and packet.feedback.evidence:
        pairs = ", ".join(f"{k}={v}" for k, v in packet.feedback.evidence.items())
        lines.append(f"- Feedback summary: {pairs}")

    execution = _execution_evidence(packet.execution_evaluations)
    if execution["samples"]:
        lines.append(
            f"- Execution evaluator: samples={execution['samples']}, "
            f"average={execution['average_score']:.2f}"
        )
    if execution["lowest_criteria"]:
        lines.append(f"- Weakest execution criteria: {', '.join(execution['lowest_criteria'])}")

    return "\n".join(lines) if lines else "- No structured evidence available."


def _execution_evidence(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise evaluator scores and the lowest-scoring contract criteria."""
    scores: list[float] = []
    criteria_totals: dict[str, list[float]] = {}
    for item in evaluations:
        if item.get("aggregate_score") is not None:
            scores.append(float(item["aggregate_score"]))
        for name, score in (item.get("criteria_scores") or {}).items():
            try:
                criteria_totals.setdefault(name, []).append(float(score))
            except (TypeError, ValueError):
                continue
    averages = {name: sum(values) / len(values) for name, values in criteria_totals.items()}
    return {
        "samples": len(scores),
        "average_score": (sum(scores) / len(scores)) if scores else None,
        "lowest_criteria": sorted(averages, key=averages.get)[:3],
    }
