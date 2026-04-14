"""Tests for agent/pattern_detector.py."""

import pytest

from agent.pattern_detector import DetectedPattern, PatternDetector, _parse_response, _slugify
from tests.conftest import MockJudge


class TestParseResponse:
    def test_positive_pattern(self):
        raw = (
            '{"is_skill": true, "suggested_name": "summarise-meeting", '
            '"description": "Summarise meeting notes", '
            '"confidence": 0.85, "rationale": "User asked thrice"}'
        )
        p = _parse_response(raw)
        assert p.is_skill is True
        assert p.suggested_name == "summarise-meeting"
        assert p.description == "Summarise meeting notes"
        assert p.confidence == 0.85
        assert "thrice" in p.rationale

    def test_negative_pattern(self):
        raw = '{"is_skill": false, "confidence": 0.0, "rationale": "one-off"}'
        p = _parse_response(raw)
        assert p.is_skill is False
        assert p.confidence == 0.0

    def test_with_code_fence(self):
        raw = '```json\n{"is_skill": false}\n```'
        p = _parse_response(raw)
        assert p.is_skill is False

    def test_unparseable_returns_negative(self):
        p = _parse_response("not json at all")
        assert p.is_skill is False
        assert "Unparseable" in p.rationale

    def test_clamps_confidence(self):
        raw = '{"is_skill": true, "suggested_name": "x", "confidence": 1.5}'
        assert _parse_response(raw).confidence == 1.0

    def test_clamps_negative_confidence(self):
        raw = '{"is_skill": true, "suggested_name": "x", "confidence": -0.2}'
        assert _parse_response(raw).confidence == 0.0


class TestSlugify:
    def test_basic_kebab(self):
        assert _slugify("Summarise Meeting") == "summarise-meeting"

    def test_strips_special_chars(self):
        assert _slugify("Foo! @ Bar?") == "foo-bar"

    def test_collapses_dashes(self):
        assert _slugify("foo--bar") == "foo-bar"

    def test_trims_leading_trailing_dashes(self):
        assert _slugify("-foo-") == "foo"


class TestPatternDetector:
    @pytest.mark.asyncio
    async def test_detect_positive(self):
        judge = MockJudge(
            '{"is_skill": true, "suggested_name": "explain-topic", '
            '"description": "x", "confidence": 0.9, "rationale": "y"}'
        )
        detector = PatternDetector(judge)
        history = [
            {"role": "user", "content": "Explain how X works"},
            {"role": "assistant", "content": "Sure, here is X..."},
        ]
        result = await detector.detect(history)
        assert isinstance(result, DetectedPattern)
        assert result.is_skill is True
        assert result.suggested_name == "explain-topic"

    @pytest.mark.asyncio
    async def test_detect_with_existing_skills(self):
        judge = MockJudge('{"is_skill": false}')
        detector = PatternDetector(judge)
        history = [{"role": "user", "content": "test"}]
        await detector.detect(history, existing_skill_names=["foo", "bar"])
        prompt = judge.last_messages[0]["content"]
        assert "- foo" in prompt
        assert "- bar" in prompt

    @pytest.mark.asyncio
    async def test_empty_history_short_circuits(self):
        judge = MockJudge("should not be called")
        detector = PatternDetector(judge)
        result = await detector.detect([])
        assert result.is_skill is False
        assert judge.last_messages is None

    @pytest.mark.asyncio
    async def test_low_temperature_default(self):
        judge = MockJudge('{"is_skill": false}')
        detector = PatternDetector(judge)
        await detector.detect([{"role": "user", "content": "x"}])
        assert judge.last_kwargs.get("temperature") == 0.1

    @pytest.mark.asyncio
    async def test_slugifies_suggested_name(self):
        judge = MockJudge(
            '{"is_skill": true, "suggested_name": "Summarise Meeting Notes", '
            '"description": "x", "confidence": 0.9}'
        )
        detector = PatternDetector(judge)
        result = await detector.detect([{"role": "user", "content": "x"}])
        assert result.suggested_name == "summarise-meeting-notes"
