"""Tests for agent/self_evaluator.py."""

import pytest

from agent.self_evaluator import Evaluation, SelfEvaluator, _parse_response
from skills.evaluation import EvaluationContract, EvaluationCriterion, ObservableMetric
from skills.loader import Skill
from tests.conftest import MockJudge


def _make_skill() -> Skill:
    from pathlib import Path

    return Skill(
        name="incident-triage",
        description="Triage an operational incident",
        path=Path("/tmp/SKILL.md"),
        triggers=["user reports an incident"],
        anti_triggers=["user wants code"],
        instructions="Identify likely causes, evidence needed, and next actions.",
    )


def _make_contract_skill() -> Skill:
    skill = _make_skill()
    skill.evaluation_contract = EvaluationContract(
        criteria=[
            EvaluationCriterion("goal_completion", "Completes the requested outcome", 0.6),
            EvaluationCriterion("evidence_quality", "Uses evidence", 0.4),
        ],
        observable_metrics=[
            ObservableMetric("tool_verification_used", "Tool evidence was checked", "tool_trace")
        ],
        failure_modes=["skipped_required_verification"],
    )
    return skill


class TestParseResponse:
    def test_clean_json(self):
        score, reasoning = _parse_response('{"score": 0.8, "reasoning": "Good output"}')
        assert score == 0.8
        assert reasoning == "Good output"

    def test_with_code_fence(self):
        raw = '```json\n{"score": 0.5, "reasoning": "Mediocre"}\n```'
        score, reasoning = _parse_response(raw)
        assert score == 0.5
        assert reasoning == "Mediocre"

    def test_with_surrounding_text(self):
        raw = 'Here is my evaluation:\n{"score": 0.9, "reasoning": "Excellent"}\n'
        score, reasoning = _parse_response(raw)
        assert score == 0.9
        assert reasoning == "Excellent"

    def test_clamps_above_one(self):
        score, _ = _parse_response('{"score": 1.5, "reasoning": "x"}')
        assert score == 1.0

    def test_clamps_below_zero(self):
        score, _ = _parse_response('{"score": -0.3, "reasoning": "x"}')
        assert score == 0.0

    def test_unparseable_returns_none(self):
        score, reasoning = _parse_response("garbage non-json output")
        assert score is None
        assert "Unparseable" in reasoning

    def test_json_without_score_key_returns_none(self):
        score, reasoning = _parse_response('{"reasoning": "I cannot evaluate this response"}')
        assert score is None
        assert "no score" in reasoning

    def test_null_score_returns_none(self):
        score, reasoning = _parse_response('{"score": null, "reasoning": "x"}')
        assert score is None
        assert "Non-numeric" in reasoning


class TestSelfEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_returns_evaluation(self):
        judge = MockJudge('{"score": 0.85, "reasoning": "Solid execution of the skill"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        result = await evaluator.evaluate(skill, "Write me an article", "Here is the article...")

        assert isinstance(result, Evaluation)
        assert result.score == 0.85
        assert "Solid" in result.reasoning

    @pytest.mark.asyncio
    async def test_prompt_includes_skill_definition(self):
        judge = MockJudge('{"score": 0.5, "reasoning": "ok"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        await evaluator.evaluate(skill, "input here", "output here")

        prompt = judge.last_messages[0]["content"]
        assert "incident-triage" in prompt
        assert "Triage an operational incident" in prompt
        assert "user reports an incident" in prompt
        assert "user wants code" in prompt
        assert "input here" in prompt
        assert "output here" in prompt

    @pytest.mark.asyncio
    async def test_uses_low_temperature_by_default(self):
        judge = MockJudge('{"score": 0.5, "reasoning": "ok"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        await evaluator.evaluate(skill, "x", "y")
        assert judge.last_kwargs.get("temperature") == 0.1

    @pytest.mark.asyncio
    async def test_temperature_override(self):
        judge = MockJudge('{"score": 0.5, "reasoning": "ok"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        await evaluator.evaluate(skill, "x", "y", temperature=0.5)
        assert judge.last_kwargs.get("temperature") == 0.5

    @pytest.mark.asyncio
    async def test_truncates_long_inputs(self):
        judge = MockJudge('{"score": 0.5, "reasoning": "ok"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        long_input = "🚀" * 10000  # use a unique non-template char
        await evaluator.evaluate(skill, long_input, "out")
        prompt = judge.last_messages[0]["content"]
        # Should be truncated to ≤3000 occurrences (template has none)
        assert prompt.count("🚀") <= 3000

    @pytest.mark.asyncio
    async def test_unparseable_judge_output_marks_evaluation_invalid(self):
        judge = MockJudge("garbage non-json output")
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        result = await evaluator.evaluate(skill, "x", "y")

        assert result.valid is False
        assert result.score == 0.0
        assert "Unparseable" in result.reasoning

    @pytest.mark.asyncio
    async def test_unparseable_contract_judge_output_marks_evaluation_invalid(self):
        judge = MockJudge("garbage non-json output")
        evaluator = SelfEvaluator(judge)
        skill = _make_contract_skill()

        result = await evaluator.evaluate(skill, "x", "y", tool_trace=[])

        assert result.valid is False
        assert result.score == 0.0
        assert result.missing_observations == ["contract_evaluation_unparseable"]

    @pytest.mark.asyncio
    async def test_judge_json_without_score_marks_evaluation_invalid(self):
        judge = MockJudge('{"reasoning": "I cannot evaluate this response"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        result = await evaluator.evaluate(skill, "x", "y")

        assert result.valid is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_contract_judge_json_without_scores_marks_evaluation_invalid(self):
        judge = MockJudge('{"reasoning": "I cannot evaluate this response"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_contract_skill()

        result = await evaluator.evaluate(skill, "x", "y", tool_trace=[])

        assert result.valid is False
        assert result.score == 0.0
        assert result.missing_observations == ["contract_scores_missing"]

    @pytest.mark.asyncio
    async def test_contract_missing_aggregate_uses_weighted_criteria(self):
        judge = MockJudge(
            '{"criteria_scores": {"goal_completion": 0.8, "evidence_quality": 0.5},'
            ' "reasoning": "ok"}'
        )
        evaluator = SelfEvaluator(judge)
        skill = _make_contract_skill()

        result = await evaluator.evaluate(skill, "x", "y", tool_trace=[])

        assert result.valid is True
        assert result.score == pytest.approx(0.8 * 0.6 + 0.5 * 0.4)

    @pytest.mark.asyncio
    async def test_contract_aware_evaluation_returns_details(self):
        judge = MockJudge(
            """{
              "aggregate_score": 0.64,
              "criteria_scores": {
                "goal_completion": {"score": 0.7, "reasoning": "Mostly complete"},
                "evidence_quality": {"score": 0.55, "reasoning": "Weak evidence"}
              },
              "observed_metrics": {"tool_verification_used": "no"},
              "missing_observations": ["tool trace"],
              "reasoning": "Useful but under-verified."
            }"""
        )
        evaluator = SelfEvaluator(judge)
        skill = _make_contract_skill()

        result = await evaluator.evaluate(
            skill,
            "debug this incident",
            "Try restarting it.",
            tool_trace=[],
        )

        assert result.score == 0.64
        assert result.criteria_scores["evidence_quality"] == 0.55
        assert result.observed_metrics["tool_verification_used"] == "no"
        assert result.missing_observations == ["tool trace"]
        prompt = judge.last_messages[0]["content"]
        assert "Execution Contract" in prompt
        assert "goal_completion" in prompt

    @pytest.mark.asyncio
    async def test_discover_contract_falls_back_to_default(self):
        judge = MockJudge('{"score": 0.5, "reasoning": "not a contract"}')
        evaluator = SelfEvaluator(judge)
        skill = _make_skill()

        contract = await evaluator.discover_contract(skill, "input", "output")

        assert contract.criteria
        assert contract.criteria[0].name == "goal_completion"
