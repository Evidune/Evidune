"""Tests for agent/core.py with mocked LLM."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry
from skills.loader import parse_skill


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

        msg = InboundMessage(text="follow up", sender_id="u", channel="cli", conversation_id="conv-h")
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
