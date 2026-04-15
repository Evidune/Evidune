"""Tests for agent/core.py with mocked LLM."""

from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry


class MockLLM(LLMClient):
    def __init__(self, response: str = "Mock response"):
        self.response = response
        self.last_messages: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.last_messages = messages
        return self.response


def _write_skill(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def skill_registry(tmp_path: Path):
    _write_skill(
        tmp_path / "skills" / "greet" / "SKILL.md",
        "---\nname: greet\ndescription: Greet the user\ntags: [greeting]\n---\nSay hello warmly.",
    )
    reg = SkillRegistry()
    reg.load_directory(tmp_path / "skills")
    return reg


@pytest.fixture
def llm():
    return MockLLM("Hello! How can I help you?")


@pytest.fixture
def agent(llm, skill_registry, memory):
    return AgentCore(
        llm=llm,
        skill_registry=skill_registry,
        memory=memory,
        system_prompt="You are Aiflay, a helpful assistant.",
    )


class TestAgentCore:
    @pytest.mark.asyncio
    async def test_handle_message(self, agent: AgentCore, llm: MockLLM):
        msg = InboundMessage(
            text="Hi there!",
            sender_id="user1",
            channel="cli",
            conversation_id="conv1",
        )
        response = await agent.handle(msg)
        assert response.text == "Hello! How can I help you?"
        assert response.conversation_id == "conv1"

    @pytest.mark.asyncio
    async def test_includes_system_prompt(self, agent: AgentCore, llm: MockLLM):
        msg = InboundMessage(text="test", sender_id="u", channel="cli", conversation_id="c")
        await agent.handle(msg)
        system_msgs = [m for m in llm.last_messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert "Aiflay" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_includes_skills_in_prompt(self, agent: AgentCore, llm: MockLLM):
        msg = InboundMessage(text="greeting", sender_id="u", channel="cli", conversation_id="c")
        await agent.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "greet" in system_content
        assert "Say hello" in system_content

    @pytest.mark.asyncio
    async def test_index_skill_prompt_mode_uses_compact_skill_index(
        self, llm: MockLLM, skill_registry: SkillRegistry, memory: MemoryStore
    ):
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            system_prompt="You are Aiflay, a helpful assistant.",
            skill_prompt_mode="index",
        )
        msg = InboundMessage(text="greeting", sender_id="u", channel="cli", conversation_id="c")
        await agent.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "greet" in system_content
        assert "get_skill" in system_content
        assert "Say hello warmly." not in system_content

    @pytest.mark.asyncio
    async def test_stores_in_memory(self, agent: AgentCore, memory: MemoryStore):
        msg = InboundMessage(text="hello", sender_id="u", channel="cli", conversation_id="conv-mem")
        await agent.handle(msg)
        history = memory.get_history("conv-mem")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_includes_history(self, agent: AgentCore, llm: MockLLM, memory: MemoryStore):
        # Pre-populate history
        memory.add_message("conv-h", "user", "previous question")
        memory.add_message("conv-h", "assistant", "previous answer")

        msg = InboundMessage(
            text="follow up", sender_id="u", channel="cli", conversation_id="conv-h"
        )
        await agent.handle(msg)

        user_msgs = [m for m in llm.last_messages if m["role"] == "user"]
        assert len(user_msgs) == 2  # previous + current
        assert user_msgs[0]["content"] == "previous question"

    @pytest.mark.asyncio
    async def test_includes_facts(self, agent: AgentCore, llm: MockLLM, memory: MemoryStore):
        memory.set_fact("user.preference", "likes formal tone")
        msg = InboundMessage(text="test", sender_id="u", channel="cli", conversation_id="c-fact")
        await agent.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "formal tone" in system_content


class TestAgentWithPersona:
    @pytest.fixture
    def agent_with_persona(self, llm, skill_registry, memory):
        from pathlib import Path

        from agent.core import AgentCore
        from personas.loader import Persona
        from personas.registry import PersonaRegistry

        reg = PersonaRegistry()
        reg.register(
            Persona(
                name="老拐",
                display_name="老拐",
                body="你是老拐，知乎写作专家。说话直接，不端着。",
                default=True,
                path=Path("/tmp/PERSONA.md"),
            )
        )
        reg.register(
            Persona(
                name="formal-helper",
                display_name="Formal Helper",
                body="You speak in a polite, formal tone.",
                path=Path("/tmp/PERSONA.md"),
            )
        )
        return AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            system_prompt="",
            persona_registry=reg,
        )

    @pytest.mark.asyncio
    async def test_default_persona_injected(self, agent_with_persona, llm: MockLLM):
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c1")
        resp = await agent_with_persona.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "老拐" in system_content
        assert "知乎写作专家" in system_content
        assert resp.metadata["persona"] == "老拐"

    @pytest.mark.asyncio
    async def test_explicit_persona_via_metadata(self, agent_with_persona, llm: MockLLM):
        msg = InboundMessage(
            text="hi",
            sender_id="u",
            channel="cli",
            conversation_id="c2",
            metadata={"persona": "formal-helper"},
        )
        resp = await agent_with_persona.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "polite, formal tone" in system_content
        assert "知乎写作专家" not in system_content
        assert resp.metadata["persona"] == "formal-helper"

    @pytest.mark.asyncio
    async def test_persona_facts_isolated(
        self, agent_with_persona, llm: MockLLM, memory: MemoryStore
    ):
        memory.set_fact("style", "uses 老拐 voice", namespace="persona:老拐")
        memory.set_fact("style", "polite English", namespace="persona:formal-helper")
        memory.set_fact("global_fact", "shared across personas")

        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c3")
        await agent_with_persona.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "uses 老拐 voice" in system_content
        assert "polite English" not in system_content  # other persona's fact
        assert "shared across personas" in system_content  # global fact
