"""Skill synthesis — generate a complete SKILL.md from an emerged pattern.

Second half of the emergence pipeline. Given a DetectedPattern + the
conversation context, asks an LLM to write a complete Claude-style
SKILL.md (frontmatter + instructions + triggers + anti-triggers +
examples), then writes it to disk.

Output layout:
    <output_dir>/<skill-name>/SKILL.md

The synthesised skill is registered with status "pending_review"
in the emerged_skills table — the user is expected to inspect it
before promoting to "active".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.llm import LLMClient
from agent.pattern_detector import DetectedPattern
from agent.utils import format_conversation, strip_code_fence

DEFAULT_OUTPUT_DIR = Path.home() / ".aiflay" / "emerged_skills"


@dataclass
class SynthesisResult:
    name: str
    skill_md: str
    path: Path


_PROMPT_TEMPLATE = """You are a skill author. Turn the conversation pattern below into a complete, reusable SKILL.md following the Claude/OpenClaw skill format.

# Pattern detected

- Suggested name: {name}
- Description: {description}
- Why: {rationale}

# Source conversation

{conversation_block}

# Output spec

Return ONLY the full SKILL.md content (no surrounding prose, no code fences). It must contain:

1. YAML frontmatter with:
   - name: kebab-case identifier
   - description: one-line summary (the same as 'description' above is fine)
   - tags: list of 2-4 relevant kebab-case tags
   - triggers: list of 2-4 phrases that should activate this skill
   - anti_triggers: list of 1-3 phrases that should NOT activate it
   - outcome_metrics: false (this skill emerged from chat, not from outcome data)
2. ## Instructions section: 5-15 actionable rules an LLM should follow when invoked
3. ## Examples section with at least 1 example (### Example 1: ...)
4. ## Reference Data section (placeholder for future iteration)

Be concrete and useful. Do not include placeholder text like "TODO" or "fill this in later". The skill should work on day one.
"""


def _format_conversation(history: list[dict[str, str]]) -> str:
    return format_conversation(history, max_content_length=1200)


def _strip_code_fence(raw: str) -> str:
    """If the LLM wraps the output in ```markdown ...```, strip it."""
    return strip_code_fence(raw).strip() + "\n"


class SkillSynthesizer:
    """LLM-driven generator of full SKILL.md from a detected pattern."""

    def __init__(
        self,
        judge: LLMClient,
        output_dir: str | Path | None = None,
    ) -> None:
        self.judge = judge
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR

    async def synthesize(
        self,
        pattern: DetectedPattern,
        history: list[dict[str, str]],
        write: bool = True,
        **llm_kwargs: Any,
    ) -> SynthesisResult | None:
        """Generate and (optionally) persist a SKILL.md.

        Returns None if the pattern is not a skill or the LLM returned
        empty content.
        """
        if not pattern.is_skill or not pattern.suggested_name:
            return None

        prompt = _PROMPT_TEMPLATE.format(
            name=pattern.suggested_name,
            description=pattern.description or "(none)",
            rationale=pattern.rationale or "(none)",
            conversation_block=_format_conversation(history),
        )
        kwargs = {"temperature": 0.3, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )

        skill_md = _strip_code_fence(raw)
        if not skill_md.strip():
            return None

        skill_dir = self.output_dir / pattern.suggested_name
        skill_path = skill_dir / "SKILL.md"

        if write:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(skill_md, encoding="utf-8")

        return SynthesisResult(
            name=pattern.suggested_name,
            skill_md=skill_md,
            path=skill_path,
        )
