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
from skills.evaluation import (
    EvaluationContract,
    default_contract_for_skill,
    parse_evaluation_contract,
)
from skills.loader import Skill


@dataclass
class Evaluation:
    """A single evaluation result."""

    score: float  # 0.0 - 1.0
    reasoning: str
    raw_response: str = ""
    criteria_scores: dict[str, float] | None = None
    observed_metrics: dict[str, Any] | None = None
    missing_observations: list[str] | None = None
    contract_version: int = 1


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

_CONTRACT_EVAL_PROMPT_TEMPLATE = """You are an impartial judge evaluating an AI assistant's response against a skill-specific execution contract.

# Skill Definition

**Name**: {skill_name}
**Description**: {skill_description}

**Instructions**:
{instructions}

# Execution Contract

{contract}

# User Input

{user_input}

# Assistant Output

{assistant_output}

# Tool Trace

{tool_trace}

# Feedback Signals

{feedback}

# Evaluation Task

Score each criterion that is applicable on a 0.0 to 1.0 scale. Exclude criteria
that cannot be judged from the available evidence by marking them not_applicable.
Then provide an aggregate_score from the scored criteria.

Respond ONLY with JSON:
{{
  "aggregate_score": <number 0-1>,
  "criteria_scores": {{
    "<criterion_name>": {{"score": <number 0-1>, "reasoning": "<short reason>", "not_applicable": false}}
  }},
  "observed_metrics": {{"<metric_name>": "<observed value or signal>"}},
  "missing_observations": ["<important missing evidence>"],
  "reasoning": "<2-3 sentence explanation>"
}}
"""

_DISCOVER_CONTRACT_PROMPT_TEMPLATE = """You are designing an execution contract for a reusable AI skill.

# Skill

Name: {skill_name}
Description: {skill_description}

# Instructions

{instructions}

# Example User Input

{user_input}

# Example Assistant Output

{assistant_output}

Create a compact execution contract that can judge future executions of this
skill. Prefer objective criteria and observable signals available from user
input, assistant output, tool trace, feedback, execution metadata, or configured
metrics. Do not require external integrations that are not already available.

Respond ONLY with JSON in this shape:
{{
  "version": 1,
  "min_pass_score": 0.70,
  "rewrite_below_score": 0.55,
  "disable_below_score": 0.25,
  "min_samples_for_rewrite": 3,
  "min_samples_for_disable": 2,
  "criteria": [
    {{"name": "goal_completion", "description": "...", "weight": 0.4}}
  ],
  "observable_signals": [
    {{"name": "tool_verification_used", "description": "...", "source": "tool_trace", "weight": 0.2}}
  ],
  "failure_modes": ["hallucinated_external_state"]
}}
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
_CONTRACT_SCORE_BLOB_RE = re.compile(r"\{[\s\S]*\"aggregate_score\"[\s\S]*\}", re.DOTALL)


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


def _format_contract(contract: EvaluationContract) -> str:
    criteria = "\n".join(
        f"- {item.name} (weight={item.weight}): {item.description}" for item in contract.criteria
    )
    observables = "\n".join(
        f"- {item.name} ({item.source}, weight={item.weight}): {item.description}"
        for item in contract.observable_signals
    )
    failures = "\n".join(f"- {item}" for item in contract.failure_modes)
    return (
        f"Version: {contract.version}\n"
        f"Thresholds: pass>={contract.min_pass_score}, "
        f"rewrite_below={contract.rewrite_below_score}, "
        f"disable_below={contract.disable_below_score}\n\n"
        f"Criteria:\n{criteria or '(none)'}\n\n"
        f"Observable signals:\n{observables or '(none)'}\n\n"
        f"Failure modes:\n{failures or '(none)'}"
    )


def _format_tool_trace(tool_trace: list[dict] | None) -> str:
    if not tool_trace:
        return "(none)"
    lines = []
    for item in tool_trace[:12]:
        lines.append(
            f"- {item.get('name', '?')} error={bool(item.get('is_error'))}: "
            f"{str(item.get('result', ''))[:240]}"
        )
    return "\n".join(lines)


def _criteria_score_value(raw: Any) -> tuple[float | None, str, bool]:
    if isinstance(raw, dict):
        if raw.get("not_applicable") is True:
            return None, str(raw.get("reasoning", "")), True
        value = raw.get("score")
        reasoning = str(raw.get("reasoning", ""))
    else:
        value = raw
        reasoning = ""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None, reasoning, True
    return max(0.0, min(1.0, score)), reasoning, False


def _weighted_score(contract: EvaluationContract, criteria_scores: dict[str, float]) -> float:
    weights = {item.name: max(0.0, item.weight) for item in contract.criteria}
    if not any(weights.values()):
        weights = {name: 1.0 for name in weights}
    numerator = 0.0
    denominator = 0.0
    for name, score in criteria_scores.items():
        weight = weights.get(name, 1.0)
        numerator += score * weight
        denominator += weight
    return max(0.0, min(1.0, numerator / denominator)) if denominator else 0.0


def _parse_contract_response(
    raw: str,
    contract: EvaluationContract,
) -> tuple[float, dict[str, float], dict[str, Any], list[str], str]:
    data = parse_json_response(raw, hint_pattern=_CONTRACT_SCORE_BLOB_RE)
    if data is None:
        score, reasoning = _parse_response(raw)
        return score, {}, {}, ["contract_evaluation_unparseable"], reasoning

    raw_criteria = data.get("criteria_scores") or {}
    criteria_scores: dict[str, float] = {}
    if isinstance(raw_criteria, dict):
        for name, value in raw_criteria.items():
            score, _reasoning, not_applicable = _criteria_score_value(value)
            if not_applicable or score is None:
                continue
            criteria_scores[str(name)] = score

    if "aggregate_score" not in data and not criteria_scores and "score" in data:
        score, reasoning = _parse_response(raw)
        missing = ["contract_criteria_not_scored"]
        return score, {}, {}, missing, reasoning

    try:
        aggregate = float(data.get("aggregate_score"))
    except (TypeError, ValueError):
        aggregate = _weighted_score(contract, criteria_scores)
    aggregate = max(0.0, min(1.0, aggregate))

    observed = (
        data.get("observed_metrics") if isinstance(data.get("observed_metrics"), dict) else {}
    )
    missing_raw = data.get("missing_observations") or []
    missing = [str(item) for item in missing_raw] if isinstance(missing_raw, list) else []
    reasoning = str(data.get("reasoning", "")).strip()
    return aggregate, criteria_scores, observed, missing, reasoning


class SelfEvaluator:
    """Evaluates skill executions using a (typically different) LLM as judge."""

    def __init__(self, judge: LLMClient) -> None:
        self.judge = judge

    async def evaluate(
        self,
        skill: Skill,
        user_input: str,
        assistant_output: str,
        *,
        tool_trace: list[dict] | None = None,
        feedback: dict[str, Any] | None = None,
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
        contract = skill.evaluation_contract
        if contract is None:
            prompt = _build_prompt(skill, user_input, assistant_output)
        else:
            prompt = _CONTRACT_EVAL_PROMPT_TEMPLATE.format(
                skill_name=skill.name,
                skill_description=skill.description or "(no description)",
                instructions=(
                    skill.instructions[:2000] if skill.instructions else "(no instructions)"
                ),
                contract=_format_contract(contract),
                user_input=user_input[:3000],
                assistant_output=assistant_output[:3000],
                tool_trace=_format_tool_trace(tool_trace),
                feedback=feedback or {},
            )
        # Lower temperature for more consistent judging
        kwargs = {"temperature": 0.1, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )
        if contract is None:
            score, reasoning = _parse_response(raw)
            return Evaluation(score=score, reasoning=reasoning, raw_response=raw)
        score, criteria_scores, observed, missing, reasoning = _parse_contract_response(
            raw, contract
        )
        return Evaluation(
            score=score,
            reasoning=reasoning,
            raw_response=raw,
            criteria_scores=criteria_scores,
            observed_metrics=observed,
            missing_observations=missing,
            contract_version=contract.version,
        )

    async def discover_contract(
        self,
        skill: Skill,
        user_input: str = "",
        assistant_output: str = "",
        **llm_kwargs: Any,
    ) -> EvaluationContract:
        """Discover a skill-specific evaluation contract with safe defaults."""
        prompt = _DISCOVER_CONTRACT_PROMPT_TEMPLATE.format(
            skill_name=skill.name,
            skill_description=skill.description or "(no description)",
            instructions=skill.instructions[:2500] if skill.instructions else "(no instructions)",
            user_input=user_input[:2000],
            assistant_output=assistant_output[:2000],
        )
        kwargs = {"temperature": 0.2, **llm_kwargs}
        try:
            raw = await self.judge.complete([{"role": "user", "content": prompt}], **kwargs)
        except Exception:
            return default_contract_for_skill(skill.name, skill.description)
        data = parse_json_response(raw)
        return parse_evaluation_contract(data) or default_contract_for_skill(
            skill.name, skill.description
        )
