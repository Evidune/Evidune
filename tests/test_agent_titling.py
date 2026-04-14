"""Integration tests: AgentCore auto-titles conversations."""

from pathlib import Path

import pytest

from agent.core import AgentCore
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry
from tests.conftest import MockJudge


@pytest.fixture
def memory(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


class StubTitleGen:
    def __init__(self, title: str):
        self.title = title
        self.calls = 0

    async def generate(self, history):
        self.calls += 1
        return self.title


def _make_agent(llm, memory, title_gen=None, title_after_turns=3):
    return AgentCore(
        llm=llm,
        skill_registry=SkillRegistry(),
        memory=memory,
        title_generator=title_gen,
        title_after_turns=title_after_turns,
    )


class TestAutoTitle:
    @pytest.mark.asyncio
    async def test_no_generator_no_title(self, memory):
        llm = MockJudge("reply")
        agent = _make_agent(llm, memory, title_gen=None)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(5):
            await agent.handle(msg)
        assert memory.get_conversation("c")["title"] == ""

    @pytest.mark.asyncio
    async def test_titles_after_enough_turns(self, memory):
        llm = MockJudge("reply")
        gen = StubTitleGen("Short Talk About Foo")
        agent = _make_agent(llm, memory, title_gen=gen, title_after_turns=2)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        # 2 turns = 4 messages ≥ title_after_turns * 2
        await agent.handle(msg)
        await agent.handle(msg)
        assert memory.get_conversation("c")["title"] == "Short Talk About Foo"
        assert gen.calls == 1  # only generated once

    @pytest.mark.asyncio
    async def test_skips_after_already_titled(self, memory):
        llm = MockJudge("reply")
        gen = StubTitleGen("First")
        agent = _make_agent(llm, memory, title_gen=gen, title_after_turns=1)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        await agent.handle(msg)
        await agent.handle(msg)
        # First call should set title; second should no-op
        assert memory.get_conversation("c")["title"] == "First"
        assert gen.calls == 1

    @pytest.mark.asyncio
    async def test_generator_failure_does_not_break(self, memory):
        class BoomGen:
            async def generate(self, history):
                raise RuntimeError("crash")

        llm = MockJudge("reply")
        agent = _make_agent(llm, memory, title_gen=BoomGen(), title_after_turns=1)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        await agent.handle(msg)
        assert resp.text == "reply"
        assert memory.get_conversation("c")["title"] == ""
