"""Integration tests: AgentCore triggers fact extraction every N turns."""

from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from gateway.base import InboundMessage
from memory.store import MemoryStore
from personas.loader import Persona
from personas.registry import PersonaRegistry
from skills.registry import SkillRegistry


class MockLLM(LLMClient):
    def __init__(self, response: str = "ok"):
        self.response = response
        self.last_messages = None

    async def complete(self, messages, **kwargs):
        self.last_messages = messages
        return self.response


class MockExtractor:
    """Stub FactExtractor that emits configured facts each call."""

    def __init__(self, facts_to_emit):
        self.facts_to_emit = facts_to_emit
        self.call_count = 0

    async def extract(self, history, existing_facts=None, **kwargs):
        self.call_count += 1
        return self.facts_to_emit


@pytest.fixture
def memory(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def llm():
    return MockLLM("response text")


def _make_agent(llm, memory, *, extractor=None, every_n=5, min_conf=0.7, persona=None):
    persona_reg = PersonaRegistry()
    if persona:
        persona_reg.register(persona)
    return AgentCore(
        llm=llm,
        skill_registry=SkillRegistry(),
        memory=memory,
        persona_registry=persona_reg,
        fact_extractor=extractor,
        fact_extraction_every_n_turns=every_n,
        fact_extraction_min_confidence=min_conf,
    )


class TestExtractionTrigger:
    @pytest.mark.asyncio
    async def test_no_extractor_means_no_extraction(self, llm, memory):
        agent = _make_agent(llm, memory, extractor=None)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(10):
            resp = await agent.handle(msg)
        assert resp.metadata["facts_extracted"] == 0

    @pytest.mark.asyncio
    async def test_extracts_every_n_turns(self, llm, memory):
        from agent.fact_extractor import ExtractedFact

        extractor = MockExtractor([ExtractedFact(key="user.x", value="y", confidence=0.9)])
        agent = _make_agent(llm, memory, extractor=extractor, every_n=3)

        msg = InboundMessage(text="t", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(6):
            await agent.handle(msg)

        # Triggered at turn 3 and turn 6 = 2 extraction calls
        assert extractor.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_trigger_before_n(self, llm, memory):
        from agent.fact_extractor import ExtractedFact

        extractor = MockExtractor([ExtractedFact(key="x", value="y", confidence=0.9)])
        agent = _make_agent(llm, memory, extractor=extractor, every_n=5)

        msg = InboundMessage(text="t", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(4):
            await agent.handle(msg)
        assert extractor.call_count == 0


class TestExtractionPersistence:
    @pytest.mark.asyncio
    async def test_high_confidence_facts_saved(self, llm, memory):
        from agent.fact_extractor import ExtractedFact

        extractor = MockExtractor(
            [
                ExtractedFact(key="user.name", value="Alice", confidence=0.95),
                ExtractedFact(key="user.lang", value="zh", confidence=0.5),  # below threshold
            ]
        )
        agent = _make_agent(llm, memory, extractor=extractor, every_n=1, min_conf=0.7)

        msg = InboundMessage(text="hi I'm Alice", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)

        assert resp.metadata["facts_extracted"] == 1
        assert memory.get_fact("user.name") == "Alice"
        assert memory.get_fact("user.lang") is None  # filtered

    @pytest.mark.asyncio
    async def test_persona_namespace_isolation(self, llm, memory):
        from agent.fact_extractor import ExtractedFact

        extractor = MockExtractor([ExtractedFact(key="style", value="formal", confidence=0.9)])
        persona = Persona(
            name="alice",
            display_name="Alice",
            body="...",
            default=True,
            path=Path("/tmp/PERSONA.md"),
        )
        agent = _make_agent(llm, memory, extractor=extractor, every_n=1, persona=persona)

        msg = InboundMessage(text="x", sender_id="u", channel="cli", conversation_id="c")
        await agent.handle(msg)

        # Saved into persona namespace, not global
        assert memory.get_fact("style", namespace="persona:alice") == "formal"
        assert memory.get_fact("style", namespace="") is None

    @pytest.mark.asyncio
    async def test_extractor_failure_does_not_break_response(self, llm, memory):
        class FailingExtractor:
            async def extract(self, *a, **kw):
                raise RuntimeError("boom")

        agent = _make_agent(llm, memory, extractor=FailingExtractor(), every_n=1)
        msg = InboundMessage(text="x", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        # Response still returned, no facts saved
        assert resp.text == "response text"
        assert resp.metadata["facts_extracted"] == 0
