"""Tests for agent/tools/ (base, registry, internal, external)."""

from pathlib import Path

import pytest

from agent.tools.base import CompletionResult, Tool, ToolCall, ToolResult
from agent.tools.external import ExternalToolsConfig, external_tools
from agent.tools.internal import (
    conversation_tools,
    identity_tools,
    memory_tools,
    plan_tools,
    skill_tools,
)
from agent.tools.registry import ToolRegistry
from identities.loader import Identity
from identities.registry import IdentityRegistry
from memory.store import MemoryStore
from skills.loader import Skill
from skills.registry import SkillRegistry

# --- Registry + execute ---


class TestToolRegistry:
    @pytest.mark.asyncio
    async def test_register_and_execute_simple(self):
        async def greet(name: str) -> str:
            return f"Hello {name}"

        reg = ToolRegistry()
        reg.register(
            Tool(
                name="greet",
                description="Say hello",
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
                handler=greet,
            )
        )
        result = await reg.execute(ToolCall(id="1", name="greet", arguments={"name": "Alice"}))
        assert not result.is_error
        assert result.content == "Hello Alice"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        reg = ToolRegistry()
        result = await reg.execute(ToolCall(id="1", name="ghost", arguments={}))
        assert result.is_error
        assert "Unknown tool" in result.content

    @pytest.mark.asyncio
    async def test_handler_exception_captured(self):
        async def explodes() -> str:
            raise RuntimeError("boom")

        reg = ToolRegistry()
        reg.register(Tool(name="explodes", description="x", parameters={}, handler=explodes))
        result = await reg.execute(ToolCall(id="1", name="explodes", arguments={}))
        assert result.is_error
        assert "boom" in result.content

    @pytest.mark.asyncio
    async def test_bad_args_captured(self):
        async def typed(x: int) -> str:
            return str(x)

        reg = ToolRegistry()
        reg.register(Tool(name="typed", description="x", parameters={}, handler=typed))
        result = await reg.execute(ToolCall(id="1", name="typed", arguments={"y": "oops"}))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_json_encoded_result(self):
        async def get_list() -> list[dict]:
            return [{"a": 1}]

        reg = ToolRegistry()
        reg.register(Tool(name="get_list", description="x", parameters={}, handler=get_list))
        result = await reg.execute(ToolCall(id="1", name="get_list", arguments={}))
        assert '"a": 1' in result.content

    def test_registry_len(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register(Tool(name="x", description="", parameters={}, handler=None))
        assert len(reg) == 1


# --- Internal tools ---


@pytest.fixture
def memory(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_set_and_get_fact(self, memory: MemoryStore):
        tools = {t.name: t for t in memory_tools(memory)}
        await tools["set_fact"].handler(key="user.name", value="Alice")
        got = await tools["get_fact"].handler(key="user.name")
        assert got == "Alice"

    @pytest.mark.asyncio
    async def test_get_missing_fact(self, memory: MemoryStore):
        tools = {t.name: t for t in memory_tools(memory)}
        got = await tools["get_fact"].handler(key="nonexistent")
        assert "no fact" in got

    @pytest.mark.asyncio
    async def test_search_facts(self, memory: MemoryStore):
        memory.set_fact("user.name", "Alice")
        memory.set_fact("project.lang", "Python")
        tools = {t.name: t for t in memory_tools(memory)}
        hits = await tools["search_facts"].handler(query="Alice")
        assert len(hits) == 1
        assert hits[0]["key"] == "user.name"

    @pytest.mark.asyncio
    async def test_list_facts_with_prefix(self, memory: MemoryStore):
        memory.set_fact("user.name", "Alice")
        memory.set_fact("user.role", "dev")
        memory.set_fact("project.lang", "Python")
        tools = {t.name: t for t in memory_tools(memory)}
        hits = await tools["list_facts"].handler(prefix="user.")
        keys = {h["key"] for h in hits}
        assert keys == {"user.name", "user.role"}

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, memory: MemoryStore):
        alice_tools = {t.name: t for t in memory_tools(memory, namespace="identity:alice")}
        bob_tools = {t.name: t for t in memory_tools(memory, namespace="identity:bob")}
        await alice_tools["set_fact"].handler(key="x", value="A")
        await bob_tools["set_fact"].handler(key="x", value="B")
        assert await alice_tools["get_fact"].handler(key="x") == "A"
        assert await bob_tools["get_fact"].handler(key="x") == "B"

    def test_read_only_memory_tools_omit_set_fact(self, memory: MemoryStore):
        tools = {t.name: t for t in memory_tools(memory, allow_write=False)}
        assert "set_fact" not in tools


class TestSkillTools:
    @pytest.mark.asyncio
    async def test_list_and_get_skill(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="writer",
                description="Write text",
                path=Path("/tmp/SKILL.md"),
                instructions="Body here",
                triggers=["user wants text"],
            )
        )
        tools = {t.name: t for t in skill_tools(reg)}
        listing = await tools["list_skills"].handler()
        assert listing == [{"name": "writer", "description": "Write text"}]
        full = await tools["get_skill"].handler(name="writer")
        assert "writer" in full and "Body here" in full


class TestIdentityTools:
    @pytest.mark.asyncio
    async def test_list_get_and_read_identity(self, tmp_path: Path):
        root = tmp_path / "identities" / "general-assistant"
        root.mkdir(parents=True)
        (root / "SOUL.md").write_text("Stay direct.", encoding="utf-8")
        (root / "IDENTITY.md").write_text(
            "---\nname: general-assistant\ndescription: Help with general tasks\ndefault: true\n---\n\nYou are Evidune.\n",
            encoding="utf-8",
        )
        (root / "USER.md").write_text("Collaborate closely with the user.", encoding="utf-8")
        (root / "TOOLS.md").write_text("Prefer verified tool output.", encoding="utf-8")

        registry = IdentityRegistry()
        registry.register(
            Identity(
                name="general-assistant",
                display_name="general-assistant",
                description="Help with general tasks",
                default=True,
                soul="Stay direct.",
                identity="You are Evidune.",
                user="Collaborate closely with the user.",
                tools="Prefer verified tool output.",
                path=root,
            )
        )
        tools = {t.name: t for t in identity_tools(registry)}

        listing = await tools["list_identities"].handler()
        assert listing == [
            {
                "name": "general-assistant",
                "display_name": "general-assistant",
                "description": "Help with general tasks",
                "language": "",
                "voice": "",
                "default": True,
            }
        ]

        full = await tools["get_identity"].handler(name="general-assistant")
        assert "# general-assistant" in full
        assert "Stay direct." in full
        assert "Collaborate closely with the user." in full

        raw = await tools["read_identity_file"].handler(
            identity_name="general-assistant",
            file="IDENTITY.md",
        )
        assert "You are Evidune." in raw

    @pytest.mark.asyncio
    async def test_read_identity_file_rejects_unknown_file(self):
        registry = IdentityRegistry()
        registry.register(Identity(name="demo", path=Path("/tmp/demo")))
        tools = {t.name: t for t in identity_tools(registry)}

        result = await tools["read_identity_file"].handler(
            identity_name="demo",
            file="secret.txt",
        )
        assert "unsupported identity file" in result


class TestConversationTools:
    @pytest.mark.asyncio
    async def test_list_conversations(self, memory: MemoryStore):
        memory.add_message("c1", "user", "hi")
        memory.add_message("c2", "user", "there")
        tools = {t.name: t for t in conversation_tools(memory, current_conversation_id="c1")}
        listing = await tools["list_conversations"].handler(limit=10)
        ids = {c["id"] for c in listing}
        assert ids == {"c1", "c2"}
        assert any(c["is_current"] for c in listing)


class TestPlanTools:
    @pytest.mark.asyncio
    async def test_get_plan_defaults_to_empty(self, memory: MemoryStore):
        tools = {t.name: t for t in plan_tools(memory, current_conversation_id="c1")}
        result = await tools["get_plan"].handler()
        assert result == {"conversation_id": "c1", "explanation": "", "items": []}

    @pytest.mark.asyncio
    async def test_update_and_clear_plan(self, memory: MemoryStore):
        tools = {t.name: t for t in plan_tools(memory, current_conversation_id="c1")}
        updated = await tools["update_plan"].handler(
            explanation="Ship the change safely.",
            plan=[
                {"step": "Inspect the current implementation", "status": "completed"},
                {"step": "Add plan tools", "status": "in_progress"},
            ],
        )
        assert updated["ok"] is True
        assert updated["items"][1]["status"] == "in_progress"

        cleared = await tools["clear_plan"].handler()
        assert cleared["ok"] is True
        assert memory.get_conversation_plan("c1") is None


# --- External tools ---


class TestExternalTools:
    @pytest.fixture
    def sandbox(self, tmp_path: Path):
        return tmp_path

    @pytest.fixture
    def tools(self, sandbox):
        return {t.name: t for t in external_tools(sandbox, ExternalToolsConfig())}

    @pytest.mark.asyncio
    async def test_read_file(self, sandbox: Path, tools):
        f = sandbox / "hello.txt"
        f.write_text("world")
        got = await tools["read_file"].handler(path="hello.txt")
        assert got == "world"

    @pytest.mark.asyncio
    async def test_write_file(self, sandbox: Path, tools):
        await tools["write_file"].handler(path="sub/new.txt", content="hi")
        assert (sandbox / "sub" / "new.txt").read_text() == "hi"

    @pytest.mark.asyncio
    async def test_write_file_size_limit(self, sandbox: Path, tools):
        huge = "x" * 600_000
        result = await tools["write_file"].handler(path="big.txt", content=huge)
        assert "exceeds" in result

    @pytest.mark.asyncio
    async def test_edit_file(self, sandbox: Path, tools):
        f = sandbox / "e.txt"
        f.write_text("hello world")
        await tools["edit_file"].handler(path="e.txt", old="world", new="there")
        assert f.read_text() == "hello there"

    @pytest.mark.asyncio
    async def test_path_escape_blocked(self, sandbox: Path, tools):
        with pytest.raises(ValueError):
            await tools["read_file"].handler(path="../../../etc/passwd")

    @pytest.mark.asyncio
    async def test_run_shell_basic(self, sandbox: Path, tools):
        (sandbox / "a.txt").write_text("x")
        (sandbox / "b.txt").write_text("x")
        result = await tools["run_shell"].handler(command="ls")
        assert "a.txt" in result
        assert "b.txt" in result
        assert "exit_code=0" in result

    @pytest.mark.asyncio
    async def test_run_shell_timeout(self, sandbox: Path):
        cfg = ExternalToolsConfig(shell_timeout_s=1)
        tools = {t.name: t for t in external_tools(sandbox, cfg)}
        result = await tools["run_shell"].handler(command="sleep 5")
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_grep(self, sandbox: Path, tools):
        (sandbox / "f.txt").write_text("foo\nbar\nfoobar\n")
        hits = await tools["grep"].handler(pattern="foo")
        texts = {h["text"] for h in hits}
        assert "foo" in texts and "foobar" in texts

    @pytest.mark.asyncio
    async def test_glob(self, sandbox: Path, tools):
        (sandbox / "a.py").touch()
        (sandbox / "b.py").touch()
        (sandbox / "c.txt").touch()
        matches = await tools["glob"].handler(pattern="*.py")
        assert len(matches) == 2

    @pytest.mark.asyncio
    async def test_execute_python(self, sandbox: Path, tools):
        result = await tools["execute_python"].handler(code="print(2 + 2)")
        assert "4" in result
        assert "exit_code=0" in result


# --- CompletionResult semantics ---


class TestCompletionResult:
    def test_is_final_when_no_tool_calls(self):
        assert CompletionResult(text="hi").is_final is True

    def test_not_final_when_tool_calls(self):
        assert (
            CompletionResult(
                text="",
                tool_calls=[ToolCall(id="1", name="x", arguments={})],
            ).is_final
            is False
        )


# --- Basic ToolResult dataclass ---


class TestToolResult:
    def test_default_not_error(self):
        r = ToolResult(tool_call_id="1", content="ok")
        assert r.is_error is False
