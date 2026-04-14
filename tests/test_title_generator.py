"""Tests for agent/title_generator.py."""

import pytest

from agent.title_generator import TitleGenerator, _clean_title
from tests.conftest import MockJudge


class TestCleanTitle:
    def test_strips_quotes(self):
        assert _clean_title('"Hello World"') == "Hello World"

    def test_strips_trailing_period(self):
        assert _clean_title("My Chat.") == "My Chat"

    def test_strips_code_fence(self):
        assert _clean_title("```\nMy Title\n```") == "My Title"

    def test_handles_multiline_returns_first(self):
        assert _clean_title("Best Title\nother garbage") == "Best Title"

    def test_caps_length(self):
        long = "word " * 50
        out = _clean_title(long)
        assert len(out) <= 80

    def test_collapses_whitespace(self):
        assert _clean_title("Too    many   spaces") == "Too many spaces"


class TestTitleGenerator:
    @pytest.mark.asyncio
    async def test_generate_returns_title(self):
        llm = MockJudge("Discussion About Python Async")
        tg = TitleGenerator(llm)
        history = [
            {"role": "user", "content": "How does asyncio work?"},
            {"role": "assistant", "content": "It uses coroutines..."},
        ]
        title = await tg.generate(history)
        assert title == "Discussion About Python Async"

    @pytest.mark.asyncio
    async def test_empty_history_returns_empty(self):
        llm = MockJudge("should not be used")
        tg = TitleGenerator(llm)
        assert await tg.generate([]) == ""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        class BoomLLM:
            async def complete(self, *a, **kw):
                raise RuntimeError("boom")

        tg = TitleGenerator(BoomLLM())
        result = await tg.generate([{"role": "user", "content": "x"}])
        assert result == ""

    @pytest.mark.asyncio
    async def test_prompt_includes_history(self):
        llm = MockJudge("My Title")
        tg = TitleGenerator(llm)
        await tg.generate([{"role": "user", "content": "unique-phrase-xyz"}])
        prompt = llm.last_messages[0]["content"]
        assert "unique-phrase-xyz" in prompt
