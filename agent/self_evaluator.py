"""Skill execution self-evaluator using a separate LLM as judge.

To avoid LLM self-justification bias, the evaluator should use a
DIFFERENT model from the one that generated the output. For example:
  - Agent uses Claude → evaluator uses GPT-4
  - Agent uses GPT → evaluator uses Claude
  - Agent uses local model → evaluator uses a hosted model

The evaluator scores (skill, input, output) on a 0-1 scale, with
reasoning. Scores are persisted to memory.store.skill_executions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.llm import LLMClient
from agent.utils import parse_json_response
from skills.loader import Skill


@dataclass
class Evaluation:
    """A single evaluation result."""

    score: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""


_EVAL_PROMPT_TEMPLATE = """You are an impartial judge evaluating the quality of an AI assistant's response against a defined skill.

# Skill Definition

**Name**: {skill_name}
**Description**: {skill_description}

**Triggers** (when this skill should be used):
{triggers_block}

**Anti-Triggers** (when this skill should NOT be used):
{anti_triggers_block}

**Instructions**:
{instructions}

# User Input

{user_input}

# Assistant Output

{assistant_output}

# Evaluation Task

Score the assistant's output on a scale from 0.0 to 1.0:
- 1.0 = perfectly executes the skill, follows all instructions, produces excellent output
- 0.7 = good execution with minor issues
- 0.5 = partial execution, noticeable problems
- 0.3 = poor execution, mostly fails the skill
- 0.0 = completely fails or violates the skill

Respond ONLY with a JSON object in this exact format:
{{"score": <number 0-1>, "reasoning": "<2-3 sentence explanation>"}}
"""


def _format_list_block(items: list[str]) -> str:
    if not items:
        return "(none specified)"
    return "\n".join(f"- {item}" for item in items)


def _build_prompt(skill: Skill, user_input: str, assistant_output: str) -> str:
    return _EVAL_PROMPT_TEMPLATE.format(
        skill_name=skill.name,
        skill_description=skill.description or "(no description)",
        triggers_block=_format_list_block(skill.triggers),
        anti_triggers_block=_format_list_block(skill.anti_triggers),
        instructions=skill.instructions[:2000] if skill.instructions else "(no instructions)",
        user_input=user_input[:3000],
        assistant_output=assistant_output[:3000],
    )


_SCORE_BLOB_RE = re.compile(r"\{[^{}]*\"score\"[^{}]*\}", re.DOTALL)


def _parse_response(raw: str) -> tuple[float, str]:
    """Parse JSON {score, reasoning} from the LLM response.

    Tolerant of surrounding text or markdown code fences.
    """
    data = parse_json_response(raw, hint_pattern=_SCORE_BLOB_RE)
    if data is None:
        return 0.0, f"Unparseable evaluator response: {raw[:200]}"
    score = float(data.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    reasoning = str(data.get("reasoning", "")).strip()
    return score, reasoning


class SelfEvaluator:
    """Evaluates skill executions using a (typically different) LLM as judge."""

    def __init__(self, judge: LLMClient) -> None:
        self.judge = judge

    async def evaluate(
        self,
        skill: Skill,
        user_input: str,
        assistant_output: str,
        **llm_kwargs: Any,
    ) -> Evaluation:
        """Score one skill execution.

        Args:
            skill: The skill definition that was used.
            user_input: The original user message.
            assistant_output: The assistant's response.
            **llm_kwargs: Forwarded to the judge LLM (temperature, etc.).

        Returns:
            Evaluation with score (0-1) and reasoning.
        """
        prompt = _build_prompt(skill, user_input, assistant_output)
        # Lower temperature for more consistent judging
        kwargs = {"temperature": 0.1, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )
        score, reasoning = _parse_response(raw)
        return Evaluation(score=score, reasoning=reasoning, raw_response=raw)
