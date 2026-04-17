"""Tests for agent/fact_extractor.py."""

import pytest

from agent.fact_extractor import (
    ExtractedFact,
    FactExtractor,
    _format_conversation,
    _format_existing,
    _parse_response,
)
from memory.store import Fact
from tests.conftest import MockJudge


class TestParseResponse:
    def test_clean_json(self):
        raw = '{"facts": [{"key": "user.name", "value": "Alice", "confidence": 0.9}]}'
        result = _parse_response(raw)
        assert len(result) == 1
        assert result[0].key == "user.name"
        assert result[0].value == "Alice"
        assert result[0].confidence == 0.9

    def test_with_code_fence(self):
        raw = '```json\n{"facts": [{"key": "x", "value": "y", "confidence": 0.5}]}\n```'
        result = _parse_response(raw)
        assert len(result) == 1
        assert result[0].key == "x"

    def test_empty_facts(self):
        result = _parse_response('{"facts": []}')
        assert result == []

    def test_unparseable_returns_empty(self):
        assert _parse_response("garbage") == []

    def test_skips_missing_key(self):
        raw = '{"facts": [{"value": "v", "confidence": 1.0}]}'
        assert _parse_response(raw) == []

    def test_skips_missing_value(self):
        raw = '{"facts": [{"key": "k", "confidence": 1.0}]}'
        assert _parse_response(raw) == []

    def test_clamps_confidence(self):
        raw = '{"facts": [{"key": "k", "value": "v", "confidence": 1.5}]}'
        assert _parse_response(raw)[0].confidence == 1.0

    def test_handles_surrounding_text(self):
        raw = (
            "Here is the analysis:\n"
            '{"facts": [{"key": "k", "value": "v", "confidence": 0.8}]}\n'
            "Done."
        )
        result = _parse_response(raw)
        assert len(result) == 1

    def test_handles_multiple_facts(self):
        raw = (
            '{"facts": ['
            '{"key": "user.name", "value": "Alice", "confidence": 0.95},'
            '{"key": "user.role", "value": "engineer", "confidence": 0.8}'
            "]}"
        )
        result = _parse_response(raw)
        assert len(result) == 2


class TestFormatHelpers:
    def test_format_existing_empty(self):
        assert _format_existing([]) == "(none)"

    def test_format_existing_with_facts(self):
        facts = [
            Fact(key="user.name", value="Alice", source="agent"),
            Fact(key="project", value="Evidune", source="auto"),
        ]
        out = _format_existing(facts)
        assert "user.name: Alice" in out
        assert "project: Evidune" in out

    def test_format_conversation_empty(self):
        assert _format_conversation([]) == "(empty)"

    def test_format_conversation_truncates_long(self):
        history = [{"role": "user", "content": "x" * 1000}]
        out = _format_conversation(history)
        assert "…" in out
        assert "[user]" in out


class TestFactExtractor:
    @pytest.mark.asyncio
    async def test_extract_returns_facts(self):
        judge = MockJudge('{"facts": [{"key": "user.name", "value": "Alice", "confidence": 0.9}]}')
        extractor = FactExtractor(judge)
        history = [
            {"role": "user", "content": "Hi I'm Alice"},
            {"role": "assistant", "content": "Hello Alice!"},
        ]
        result = await extractor.extract(history)
        assert len(result) == 1
        assert result[0].key == "user.name"

    @pytest.mark.asyncio
    async def test_empty_history_skips_call(self):
        judge = MockJudge("should not be called")
        extractor = FactExtractor(judge)
        result = await extractor.extract([])
        assert result == []
        assert judge.last_messages is None

    @pytest.mark.asyncio
    async def test_existing_facts_in_prompt(self):
        judge = MockJudge('{"facts": []}')
        extractor = FactExtractor(judge)
        existing = [Fact(key="user.name", value="Alice", source="agent")]
        history = [{"role": "user", "content": "test"}]
        await extractor.extract(history, existing_facts=existing)
        prompt = judge.last_messages[0]["content"]
        assert "user.name: Alice" in prompt

    @pytest.mark.asyncio
    async def test_low_temperature_default(self):
        judge = MockJudge('{"facts": []}')
        extractor = FactExtractor(judge)
        await extractor.extract([{"role": "user", "content": "x"}])
        assert judge.last_kwargs.get("temperature") == 0.1

    @pytest.mark.asyncio
    async def test_dataclass_round_trip(self):
        f = ExtractedFact(key="k", value="v", confidence=0.8)
        assert f.key == "k" and f.value == "v" and f.confidence == 0.8
