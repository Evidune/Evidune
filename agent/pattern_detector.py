"""Pattern detection — decide whether a conversation contains a reusable skill.

This is the FIRST half of the emergence pipeline. It runs an LLM
over recent conversation turns and asks: "is there a reusable
pattern here that should become a Skill?"

If yes (confidence >= threshold), `skill_synthesizer.py` is then
invoked to generate the full SKILL.md.

Keeping detection separate from synthesis lets us:
- Run detection cheaply (small prompt, small output)
- Skip the expensive synthesis call when nothing emerges
- Apply different thresholds at each stage
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.llm import LLMClient
from agent.utils import format_conversation, format_skill_names, parse_json_response


@dataclass
class DetectedPattern:
    is_skill: bool
    suggested_name: str = ""
    description: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    rationale: str = ""


_PROMPT_TEMPLATE = """You are a pattern detector for an AI agent's skill library.

Look at the recent conversation. Decide whether it contains a reusable pattern that should be saved as a new Skill.

A Skill is reusable when:
- The user's request is likely to recur (not one-off)
- The assistant's approach has a repeatable structure (steps, rules, format)
- A future user asking a similar question would benefit from the same recipe
- The conversation is designing, refining, or operationalising a reusable skill/capability
  package, even if the current chat does not execute the capability end-to-end

Treat conversations about "make this into a reusable skill/capability/workflow"
as valid skill candidates when the output would help future conversations.
If earlier turns discuss boundaries, limitations, or missing tools but later
turns explicitly ask to build a reusable skill, prioritise the final intended
capability rather than the earlier limitation discussion.
Name the skill after the reusable job to be done, not after the assistant's
temporary constraints or refusals.

NOT a skill:
- One-time questions, one-off debugging for a single broken environment, casual chat
- Pure information lookup
- Generic Q&A without a durable operating procedure
- Anything already covered by an existing skill

# Existing skills (DO NOT propose duplicates)

{existing_skills_block}

# Recent conversation

{conversation_block}

# Output

Return ONLY a JSON object in this exact format:

{{
  "is_skill": true,
  "suggested_name": "snake-case-name",
  "description": "One-line summary of when to use this skill",
  "confidence": 0.85,
  "rationale": "Why this is a reusable pattern (1-2 sentences)"
}}

If no reusable pattern is present, return:

{{"is_skill": false, "confidence": 0.0, "rationale": "..."}}

Use kebab-case for suggested_name (lowercase, hyphens). Be conservative:
prefer false negative over false positive — only propose skills you are
60%+ confident about.
"""


# Backwards-compatible aliases (larger max_content for detection)
_format_skills = format_skill_names


def _format_conversation(history: list[dict[str, str]]) -> str:
    return format_conversation(history, max_content_length=1600)


def _parse_response(raw: str) -> DetectedPattern:
    data = parse_json_response(raw)
    if not isinstance(data, dict):
        return DetectedPattern(is_skill=False, rationale="Unparseable response")

    is_skill = bool(data.get("is_skill", False))
    name = str(data.get("suggested_name", "")).strip()
    description = str(data.get("description", "")).strip()
    rationale = str(data.get("rationale", "")).strip()
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    return DetectedPattern(
        is_skill=is_skill,
        suggested_name=name,
        description=description,
        confidence=conf,
        rationale=rationale,
    )


def _slugify(name: str) -> str:
    """Normalise a name to kebab-case ascii."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


class PatternDetector:
    """LLM-driven detector for reusable patterns in conversation."""

    def __init__(self, judge: LLMClient) -> None:
        self.judge = judge

    async def detect(
        self,
        history: list[dict[str, str]],
        existing_skill_names: list[str] | None = None,
        **llm_kwargs: Any,
    ) -> DetectedPattern:
        """Run pattern detection.

        Args:
            history: Recent conversation messages (role/content dicts).
            existing_skill_names: Names of skills already in the registry,
                to discourage duplicate proposals.

        Returns:
            DetectedPattern. Caller filters by confidence/threshold.
        """
        if not history:
            return DetectedPattern(is_skill=False, rationale="Empty history")

        prompt = _PROMPT_TEMPLATE.format(
            existing_skills_block=_format_skills(existing_skill_names or []),
            conversation_block=_format_conversation(history),
        )
        kwargs = {"temperature": 0.1, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )
        result = _parse_response(raw)
        if result.is_skill and result.suggested_name:
            result.suggested_name = _slugify(result.suggested_name)
        return result
