"""Tests for agent/self_evaluator.py."""

import pytest

from agent.self_evaluator import Evaluation, SelfEvaluator, _parse_response
from skills.loader import Skill
from tests.conftest import MockJudge


def _make_skill() -> Skill:
    from pathlib import Path

    return Skill(
        name="write-article",
        description="Write a Zhihu article",
        path=Path("/tmp/SKILL.md"),
        triggers=["user wants a Zhihu article"],
        anti_triggers=["user wants code"],
        instructions="Write 2000-4000 words. Be concrete.",
    )


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

    def test_unparseable_returns_zero(self):
        score, reasoning = _parse_response("garbage non-json output")
        assert score == 0.0
        assert "Unparseable" in reasoning


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
        assert "write-article" in prompt
        assert "Write a Zhihu article" in prompt
        assert "user wants a Zhihu article" in prompt
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
