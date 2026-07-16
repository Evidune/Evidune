"""Tests for agent/core.py with mocked LLM."""

from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.graph_memory import GraphMemoryService
from agent.llm import LLMClient
from agent.self_evaluator import SelfEvaluator
from agent.tools.base import CompletionResult, Tool, ToolCall
from agent.tools.registry import ToolRegistry
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.loader import Skill
from skills.registry import SkillRegistry
from tests.conftest import MockJudge


class MockLLM(LLMClient):
    def __init__(self, response: str = "Mock response"):
        self.response = response
        self.last_messages: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.last_messages = messages
        return self.response


class QueueJudge(LLMClient):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.last_messages: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.last_messages = messages
        return self.responses.pop(0)


class MockToolLLM(LLMClient):
    def __init__(self):
        self.calls = 0
        self.tool_names: list[str] = []

    async def complete(self, messages, **kwargs):
        return "done"

    async def complete_with_tools(self, messages, tools, **kwargs):
        self.tool_names = [tool.name for tool in tools]
        if self.calls == 0:
            self.calls += 1
            return CompletionResult(
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="update_plan",
                        arguments={
                            "explanation": "Implement plan support safely.",
                            "plan": [
                                {
                                    "step": "Inspect the current tool registry",
                                    "status": "completed",
                                },
                                {"step": "Add plan tools", "status": "in_progress"},
                            ],
                        },
                    )
                ]
            )
        return CompletionResult(text="done")


class InspectingToolListLLM(LLMClient):
    def __init__(self, response: str = "ok"):
        self.response = response
        self.tool_names: list[str] = []
        self.last_messages: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.last_messages = messages
        return self.response

    async def complete_with_tools(self, messages, tools, **kwargs):
        self.last_messages = messages
        self.tool_names = [tool.name for tool in tools]
        return CompletionResult(text=self.response)


class EvidenceCommitmentLLM(LLMClient):
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **kwargs):
        return "published"

    async def complete_with_tools(self, messages, tools, **kwargs):
        if self.calls == 0:
            self.calls += 1
            return CompletionResult(
                tool_calls=[
                    ToolCall(
                        id="commit-1",
                        name="commit_outcome_evidence",
                        arguments={
                            "skill_name": "greet",
                            "entity_type": "article",
                            "entity_id": "article-42",
                            "observation_plan": {
                                "probe_id": "article_status",
                                "probe_revision": "v1",
                                "evaluator_id": "published_predicate",
                                "evaluator_revision": "v1",
                                "horizons": [{"id": "next_day"}],
                            },
                            "attribution_policy": "direct",
                            "minimum_evidence_grade": "direct",
                        },
                    )
                ]
            )
        return CompletionResult(text="published")


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
        system_prompt="You are Evidune, a helpful assistant.",
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
        assert "Evidune" in system_msgs[0]["content"]

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
            system_prompt="You are Evidune, a helpful assistant.",
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
    async def test_stores_conversation_channel_from_inbound_message(
        self, agent: AgentCore, memory: MemoryStore
    ):
        msg = InboundMessage(text="hello", sender_id="u", channel="web", conversation_id="conv-web")
        await agent.handle(msg)
        assert memory.get_conversation("conv-web")["channel"] == "web"

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

    @pytest.mark.asyncio
    async def test_graph_memory_selected_skill_records_execution(
        self, tmp_path: Path, memory: MemoryStore, llm: MockLLM
    ):
        skill_path = _write_skill(
            tmp_path / "memory-helper" / "SKILL.md",
            "---\nname: memory-helper\ndescription: Long term context support\ntags: [context]\n---\n"
            "Use SQLite graph traversal when reconstructing context.",
        )
        registry = SkillRegistry()
        registry.register(
            Skill(
                name="memory-helper",
                description="Long term context support",
                path=skill_path,
                instructions="Use SQLite graph traversal when reconstructing context.",
                tags=["context"],
            )
        )
        agent = AgentCore(
            llm=llm,
            skill_registry=registry,
            memory=memory,
            graph_memory=GraphMemoryService(memory),
        )

        response = await agent.handle(
            InboundMessage(
                text="Need help with sqlite traversal",
                sender_id="u",
                channel="cli",
                conversation_id="c-graph-skill",
            )
        )

        assert "memory-helper" in response.metadata["skills"]
        assert memory.get_skill_executions("memory-helper")
        graph = response.metadata["graph_reconstruction"]
        assert graph["trace_id"]
        assert graph["selected_skills"] == ["memory-helper"]

    @pytest.mark.asyncio
    async def test_graph_memory_selected_facts_replace_full_fact_injection(
        self, memory: MemoryStore, llm: MockLLM, skill_registry: SkillRegistry
    ):
        memory.set_fact("project.graph_memory", "Use SQLite graph reconstruction")
        memory.set_fact("user.preference", "unrelated preference")
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            graph_memory=GraphMemoryService(memory),
        )

        response = await agent.handle(
            InboundMessage(
                text="How should sqlite graph reconstruction work?",
                sender_id="u",
                channel="cli",
                conversation_id="c-graph-fact",
            )
        )

        system_content = llm.last_messages[0]["content"]
        assert "Use SQLite graph reconstruction" in system_content
        assert "unrelated preference" not in system_content
        assert response.metadata["graph_reconstruction"]["evidence_count"] >= 1

    @pytest.mark.asyncio
    async def test_graph_memory_no_hit_falls_back_to_existing_fact_prompt(
        self, memory: MemoryStore, llm: MockLLM, skill_registry: SkillRegistry
    ):
        memory.set_fact("user.preference", "likes formal tone")
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            graph_memory=GraphMemoryService(memory),
        )

        response = await agent.handle(
            InboundMessage(
                text="zzzz unmatched query",
                sender_id="u",
                channel="cli",
                conversation_id="c-graph-fallback",
            )
        )

        system_content = llm.last_messages[0]["content"]
        assert "formal tone" in system_content
        assert response.metadata["graph_reconstruction"]["evidence_count"] == 0

    @pytest.mark.asyncio
    async def test_turn_scoped_plan_tools_are_available(
        self,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
    ):
        llm = MockToolLLM()
        tool_registry = ToolRegistry()
        tool_registry.register(
            Tool(
                name="noop",
                description="Keeps tool mode enabled for the test.",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )
        )
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            tool_registry=tool_registry,
        )

        response = await agent.handle(
            InboundMessage(
                text="please make a plan",
                sender_id="u",
                channel="cli",
                conversation_id="c-plan",
            )
        )

        assert "update_plan" in llm.tool_names
        assert response.metadata["mode"] == "execute"
        assert response.metadata["plan"]["items"][1]["step"] == "Add plan tools"
        assert memory.get_conversation_plan("c-plan") == {
            "explanation": "Implement plan support safely.",
            "items": [
                {"step": "Inspect the current tool registry", "status": "completed"},
                {"step": "Add plan tools", "status": "in_progress"},
            ],
        }
        assert response.metadata["tool_trace"][0]["name"] == "update_plan"

    @pytest.mark.asyncio
    async def test_plan_mode_exposes_planning_tools_only(
        self,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
    ):
        llm = InspectingToolListLLM()
        tool_registry = ToolRegistry()
        tool_registry.register(
            Tool(
                name="noop",
                description="Execution-only tool for test coverage.",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )
        )
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            tool_registry=tool_registry,
        )

        response = await agent.handle(
            InboundMessage(
                text="plan this task",
                sender_id="u",
                channel="cli",
                conversation_id="c-plan-mode",
                metadata={"mode": "plan"},
            )
        )

        assert response.metadata["mode"] == "plan"
        assert memory.get_conversation("c-plan-mode")["mode"] == "plan"
        assert "update_plan" in llm.tool_names
        assert "get_identity" in llm.tool_names
        assert "set_fact" not in llm.tool_names
        assert "noop" not in llm.tool_names
        assert "Operating Mode: Plan" in llm.last_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_mode_exposes_execution_tools(
        self,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
    ):
        llm = InspectingToolListLLM()
        tool_registry = ToolRegistry()
        tool_registry.register(
            Tool(
                name="noop",
                description="Execution-only tool for test coverage.",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )
        )
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            tool_registry=tool_registry,
        )

        response = await agent.handle(
            InboundMessage(
                text="execute this task",
                sender_id="u",
                channel="cli",
                conversation_id="c-execute-mode",
            )
        )

        assert response.metadata["mode"] == "execute"
        assert "set_fact" in llm.tool_names
        assert "get_identity" in llm.tool_names
        assert "noop" in llm.tool_names
        assert "Operating Mode: Execute" in llm.last_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_outcome_commitment_is_bound_to_exact_skill_execution(
        self,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
    ):
        llm = EvidenceCommitmentLLM()
        tool_registry = ToolRegistry()
        tool_registry.register(
            Tool(
                name="noop",
                description="Keeps execution tool mode enabled.",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )
        )
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            tool_registry=tool_registry,
        )

        response = await agent.handle(
            InboundMessage(
                text="Greet the user and publish the article",
                sender_id="u",
                channel="cli",
                conversation_id="c-evidence",
            )
        )

        assert len(response.metadata["execution_ids"]) == 1
        assert len(response.metadata["evidence_binding_ids"]) == 1
        binding = memory.get_evidence_binding(response.metadata["evidence_binding_ids"][0])
        assert binding["execution_id"] == response.metadata["execution_ids"][0]
        assert binding["skill_name"] == "greet"
        assert binding["entity_id"] == "article-42"

    @pytest.mark.asyncio
    async def test_reuses_stored_conversation_mode(self, agent: AgentCore, memory: MemoryStore):
        first = InboundMessage(
            text="plan first",
            sender_id="u",
            channel="cli",
            conversation_id="c-mode-reuse",
            metadata={"mode": "plan"},
        )
        await agent.handle(first)

        second = InboundMessage(
            text="follow up",
            sender_id="u",
            channel="cli",
            conversation_id="c-mode-reuse",
        )
        response = await agent.handle(second)

        assert response.metadata["mode"] == "plan"
        assert memory.get_conversation("c-mode-reuse")["mode"] == "plan"

    @pytest.mark.asyncio
    async def test_persists_evaluator_scores_for_matched_skills(
        self, skill_registry: SkillRegistry, memory: MemoryStore
    ):
        llm = MockLLM("Hello! How can I help you?")
        evaluator = SelfEvaluator(MockJudge('{"score": 0.82, "reasoning": "Strong match"}'))
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            self_evaluator=evaluator,
        )

        response = await agent.handle(
            InboundMessage(
                text="greeting",
                sender_id="u",
                channel="cli",
                conversation_id="c-eval",
            )
        )

        execution = memory.get_skill_executions("greet")[0]
        assert execution["score"] == 0.82
        assert execution["evaluator_reasoning"] == "Strong match"
        assert response.metadata["evaluations_recorded"] == 1

    @pytest.mark.asyncio
    async def test_unparseable_judge_output_is_not_persisted(
        self, skill_registry: SkillRegistry, memory: MemoryStore
    ):
        llm = MockLLM("Hello! How can I help you?")
        evaluator = SelfEvaluator(MockJudge("garbage non-json output"))
        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            self_evaluator=evaluator,
        )

        response = await agent.handle(
            InboundMessage(
                text="greeting",
                sender_id="u",
                channel="cli",
                conversation_id="c-eval-invalid",
            )
        )

        # Invalid evaluations never become 0.0 scores in the store.
        execution = memory.get_skill_executions("greet")[0]
        assert execution["score"] is None
        assert response.metadata["evaluations_recorded"] == 0

    @pytest.mark.asyncio
    async def test_legacy_skill_discovers_contract_and_returns_evaluation_metadata(
        self, tmp_path: Path, memory: MemoryStore
    ):
        skill_path = _write_skill(
            tmp_path / "skills" / "incident-triage" / "SKILL.md",
            "---\n"
            "name: incident-triage\n"
            "description: Triage incidents\n"
            "triggers: [incident]\n"
            "---\n"
            "\n"
            "## Instructions\n"
            "Diagnose incidents with evidence.\n",
        )
        registry = SkillRegistry()
        registry.load_directory(tmp_path / "skills")
        judge = QueueJudge(
            [
                """{
                  "version": 1,
                  "min_pass_score": 0.7,
                  "rewrite_below_score": 0.55,
                  "disable_below_score": 0.25,
                  "min_samples_for_rewrite": 3,
                  "min_samples_for_disable": 2,
                  "criteria": [
                    {"name": "goal_completion", "description": "Incident was triaged", "weight": 1.0}
                  ],
                  "observable_metrics": [],
                  "failure_modes": ["skipped_required_verification"]
                }""",
                """{
                  "aggregate_score": 0.66,
                  "criteria_scores": {
                    "goal_completion": {"score": 0.66, "reasoning": "Partial triage"}
                  },
                  "observed_metrics": {},
                  "missing_observations": ["tool trace"],
                  "reasoning": "The response identifies a path but lacks verification."
                }""",
            ]
        )
        agent = AgentCore(
            llm=MockLLM("Check logs first."),
            skill_registry=registry,
            memory=memory,
            self_evaluator=SelfEvaluator(judge),
        )

        response = await agent.handle(
            InboundMessage(
                text="incident: api is down",
                sender_id="u",
                channel="cli",
                conversation_id="c-contract",
            )
        )

        assert response.metadata["execution_evaluations"][0]["skill_name"] == "incident-triage"
        assert response.metadata["execution_evaluations"][0]["contract_status"] == "written"
        assert response.metadata["execution_evaluations"][0]["aggregate_score"] == 0.66
        assert memory.get_skill_evaluation_contract("incident-triage")["source"] == "written"
        assert "execution_contract:" in skill_path.read_text(encoding="utf-8")
        events = memory.list_skill_lifecycle_events("incident-triage")
        assert events[0]["action"] == "contract_discover"

    @pytest.mark.asyncio
    async def test_legacy_skill_runtime_contract_respects_auto_update_false(
        self, tmp_path: Path, memory: MemoryStore
    ):
        skill_path = _write_skill(
            tmp_path / "skills" / "ops-helper" / "SKILL.md",
            "---\nname: ops-helper\ndescription: Help ops\ntriggers: [ops]\n---\n"
            "\n## Instructions\nHelp with operational tasks.\n",
        )
        registry = SkillRegistry()
        registry.load_directory(tmp_path / "skills")
        judge = QueueJudge(
            [
                """{
                  "version": 1,
                  "criteria": [
                    {"name": "goal_completion", "description": "Goal completed", "weight": 1.0}
                  ],
                  "observable_metrics": [],
                  "failure_modes": []
                }""",
                """{
                  "aggregate_score": 0.8,
                  "criteria_scores": {"goal_completion": {"score": 0.8}},
                  "observed_metrics": {},
                  "missing_observations": [],
                  "reasoning": "Good enough."
                }""",
            ]
        )
        agent = AgentCore(
            llm=MockLLM("ops response"),
            skill_registry=registry,
            memory=memory,
            self_evaluator=SelfEvaluator(judge),
            skill_contract_auto_update=False,
        )

        response = await agent.handle(
            InboundMessage(
                text="ops checklist",
                sender_id="u",
                channel="cli",
                conversation_id="c-runtime-contract",
            )
        )

        assert response.metadata["execution_evaluations"][0]["contract_status"] == "runtime"
        assert memory.get_skill_evaluation_contract("ops-helper")["source"] == "runtime"
        assert "execution_contract:" not in skill_path.read_text(encoding="utf-8")
        assert memory.list_skill_lifecycle_events("ops-helper") == []

    @pytest.mark.asyncio
    async def test_prunes_rolled_back_emerged_skills_before_matching(
        self, llm: MockLLM, memory: MemoryStore
    ):
        reg = SkillRegistry()
        skill_path = Path("/tmp/rolled-back/SKILL.md")
        reg.register(
            Skill(
                name="rolled-back-skill",
                description="Should not be usable",
                path=skill_path,
                triggers=["rolled back"],
                instructions="Ignore me",
            )
        )
        memory.register_emerged_skill(
            name="rolled-back-skill",
            status="rolled_back",
            path=str(skill_path),
            reason="Bad feedback",
        )
        agent = AgentCore(llm=llm, skill_registry=reg, memory=memory)

        await agent.handle(
            InboundMessage(
                text="rolled back",
                sender_id="u",
                channel="cli",
                conversation_id="c-prune",
            )
        )

        assert reg.get("rolled-back-skill") is None

    @pytest.mark.asyncio
    async def test_negative_feedback_disables_base_skill_via_shared_governance(
        self, llm: MockLLM, tmp_path: Path, memory: MemoryStore
    ):
        reg = SkillRegistry()
        skill_path = _write_skill(
            tmp_path / "skills" / "greet" / "SKILL.md",
            "---\nname: greet\ndescription: Greet\n---\n\n## Instructions\nSay hello.\n",
        )
        reg.load_directory(tmp_path / "skills")
        agent = AgentCore(llm=llm, skill_registry=reg, memory=memory)

        # Two negative executions clear the minimum-evidence gate; a single
        # thumbs_down must never disable a skill (covered below).
        for _turn in range(2):
            await agent.handle(
                InboundMessage(
                    text="greet me",
                    sender_id="u",
                    channel="cli",
                    conversation_id="c-disable-base",
                )
            )
            execution = memory.get_skill_executions("greet")[0]
            memory.update_execution_signals(execution["id"], {"thumbs_down": True})
            memory.update_execution_score(execution["id"], 0.1, "Poor result")

        await agent.handle(
            InboundMessage(
                text="greet again",
                sender_id="u",
                channel="cli",
                conversation_id="c-disable-base",
            )
        )

        assert reg.get("greet") is None
        assert memory.get_skill_state("greet")["status"] == "disabled"
        assert skill_path.read_text(encoding="utf-8").startswith("---\nname: greet")

    @pytest.mark.asyncio
    async def test_harness_rewrite_replaces_active_skill_and_reloads_registry(
        self, llm: MockLLM, tmp_path: Path, memory: MemoryStore
    ):
        reg = SkillRegistry()
        skill_path = _write_skill(
            tmp_path / "skills" / "writer" / "SKILL.md",
            "---\nname: writer\ndescription: Write articles\n---\n"
            "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n",
        )
        reg.load_directory(tmp_path / "skills")
        agent = AgentCore(llm=llm, skill_registry=reg, memory=memory)

        # Contract evidence below rewrite_below_score drives a harness rewrite.
        for score in (0.4, 0.5, 0.5):
            execution_id = memory.record_execution(
                skill_name="writer",
                user_input="write",
                assistant_output="draft",
                cross_model_score=score,
            )
            memory.record_skill_evaluation(
                execution_id=execution_id,
                skill_name="writer",
                aggregate_score=score,
                criteria_scores={"goal_completion": score},
            )

        updated = await agent._maybe_reconcile_skill_feedback([reg.get("writer")])

        assert updated == ["writer"]
        on_disk = skill_path.read_text(encoding="utf-8")
        assert "### Outcome-Backed Adjustments" in on_disk
        assert "version: 1.0.1" in on_disk
        assert memory.get_skill_state("writer")["status"] == "observing"
        event = memory.get_latest_skill_lifecycle_event("writer", "rewrite")
        assert event["content_after"] == on_disk
        assert "Outcome-Backed Adjustments" in reg.get("writer").instructions
        assert reg.get("writer").version == "1.0.1"


class TestAgentWithIdentity:
    @pytest.fixture
    def agent_with_identity(self, llm, skill_registry, memory):
        from pathlib import Path

        from agent.core import AgentCore
        from identities.loader import Identity
        from identities.registry import IdentityRegistry

        reg = IdentityRegistry()
        reg.register(
            Identity(
                name="general-assistant",
                display_name="Evidune",
                soul="你说话直接，基于证据。",
                identity="你是 Evidune 通用任务专家。",
                user="你和用户协作完成任务。",
                default=True,
                path=Path("/tmp/identities/general-assistant"),
            )
        )
        reg.register(
            Identity(
                name="formal-helper",
                display_name="Formal Helper",
                soul="You speak in a polite, formal tone.",
                identity="You are a concise formal assistant.",
                user="The user wants clear, well-structured answers.",
                path=Path("/tmp/identities/formal-helper"),
            )
        )
        return AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            system_prompt="",
            identity_registry=reg,
        )

    @pytest.mark.asyncio
    async def test_default_identity_injected(self, agent_with_identity, llm: MockLLM):
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c1")
        resp = await agent_with_identity.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "Evidune" in system_content
        assert "通用任务专家" in system_content
        assert resp.metadata["identity"] == "general-assistant"

    @pytest.mark.asyncio
    async def test_explicit_identity_via_metadata(self, agent_with_identity, llm: MockLLM):
        msg = InboundMessage(
            text="hi",
            sender_id="u",
            channel="cli",
            conversation_id="c2",
            metadata={"identity": "formal-helper"},
        )
        resp = await agent_with_identity.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "polite, formal tone" in system_content
        assert "通用任务专家" not in system_content
        assert resp.metadata["identity"] == "formal-helper"

    @pytest.mark.asyncio
    async def test_identity_facts_isolated(
        self, agent_with_identity, llm: MockLLM, memory: MemoryStore
    ):
        memory.set_fact(
            "style", "uses evidence-first voice", namespace="identity:general-assistant"
        )
        memory.set_fact("style", "polite English", namespace="identity:formal-helper")
        memory.set_fact("global_fact", "shared across identities")

        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c3")
        await agent_with_identity.handle(msg)
        system_content = llm.last_messages[0]["content"]
        assert "uses evidence-first voice" in system_content
        assert "polite English" not in system_content  # other identity's fact
        assert "shared across identities" in system_content  # global fact

    @pytest.mark.asyncio
    async def test_persists_explicit_identity_on_conversation(
        self, agent_with_identity, memory: MemoryStore
    ):
        msg = InboundMessage(
            text="hi",
            sender_id="u",
            channel="web",
            conversation_id="c4",
            metadata={"identity": "formal-helper"},
        )
        await agent_with_identity.handle(msg)
        assert memory.get_conversation("c4")["identity"] == "formal-helper"

    @pytest.mark.asyncio
    async def test_reuses_conversation_identity_when_request_omits_it(
        self, agent_with_identity, llm: MockLLM
    ):
        first = InboundMessage(
            text="hi",
            sender_id="u",
            channel="web",
            conversation_id="c5",
            metadata={"identity": "formal-helper"},
        )
        await agent_with_identity.handle(first)

        second = InboundMessage(
            text="follow up",
            sender_id="u",
            channel="web",
            conversation_id="c5",
        )
        resp = await agent_with_identity.handle(second)

        system_content = llm.last_messages[0]["content"]
        assert "polite, formal tone" in system_content
        assert resp.metadata["identity"] == "formal-helper"
