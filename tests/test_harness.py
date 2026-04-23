"""Tests for swarm harness orchestration and iteration workflow."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from agent.skill_feedback import SkillFeedbackSummary
from agent.tools.base import CompletionResult, Tool
from agent.tools.registry import ToolRegistry
from core.iteration_harness import IterationHarness, build_decision_packet
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry


class RoleAwareLLM(LLMClient):
    def __init__(self) -> None:
        self.role_payloads: dict[str, list[str]] = {}

    async def complete(self, messages, **kwargs):
        system = messages[0]["content"]
        role = self._extract_role(system)
        self.role_payloads.setdefault(role, []).append(messages[-1]["content"])
        if role == "planner":
            return "Plan: split the task into bounded work."
        if role.startswith("worker"):
            return f"{role} completed its branch."
        if role == "critic":
            return "ACCEPT\nThe worker output satisfies the brief."
        if role == "synthesizer":
            return "Final synthesis"
        return "ok"

    async def complete_with_tools(self, messages, tools, **kwargs):
        text = await self.complete(messages, **kwargs)
        return CompletionResult(text=text)

    @staticmethod
    def _extract_role(system: str) -> str:
        for line in system.splitlines():
            if line.startswith("# Role: "):
                return line.split(":", 1)[1].split("(")[0].strip()
        return "unknown"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _registry_with_skills(tmp_path: Path, *names: str) -> SkillRegistry:
    registry = SkillRegistry()
    for name in names:
        _write(
            tmp_path / "skills" / name / "SKILL.md",
            "---\n"
            f"name: {name}\n"
            f"description: {name} helper\n"
            f"triggers: [{name.replace('-', ' ')}]\n"
            "---\n"
            "## Instructions\n"
            f"Use {name} carefully.\n",
        )
    registry.load_directory(tmp_path / "skills")
    return registry


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    yield store
    store.close()


@pytest.mark.asyncio
async def test_swarm_harness_persists_task_and_steps(tmp_path: Path, memory: MemoryStore):
    llm = RoleAwareLLM()
    registry = _registry_with_skills(tmp_path, "greet")
    agent = AgentCore(
        llm=llm,
        skill_registry=registry,
        memory=memory,
        harness_config=SimpleNamespace(
            strategy="swarm",
            simple_turn_threshold=1,
            default_squad="general",
            max_worker_branches=2,
            max_rounds=2,
            token_budget=20000,
            tool_call_budget=16,
            wall_clock_budget_s=120,
            stream_events=True,
        ),
    )

    response = await agent.handle(
        InboundMessage(
            text="implement a greeting workflow",
            sender_id="u",
            channel="cli",
            conversation_id="c-swarm",
        )
    )

    assert response.text == "Final synthesis"
    assert response.metadata["task_id"]
    assert response.metadata["squad"] == "execution"
    task = memory.get_harness_task(response.metadata["task_id"])
    assert task is not None
    assert task["status"] == response.metadata["task_status"]
    steps = memory.list_harness_steps(response.metadata["task_id"])
    assert [step["phase"] for step in steps] == [
        "plan",
        "execute",
        "validate",
        "critique",
        "finalise",
    ]
    assert response.metadata["task_events"]


@pytest.mark.asyncio
async def test_worker_skill_assignment_is_scoped_per_branch(tmp_path: Path, memory: MemoryStore):
    llm = RoleAwareLLM()
    registry = _registry_with_skills(tmp_path, "alpha-skill", "beta-skill")
    agent = AgentCore(
        llm=llm,
        skill_registry=registry,
        memory=memory,
        harness_config=SimpleNamespace(
            strategy="swarm",
            simple_turn_threshold=1,
            default_squad="general",
            max_worker_branches=2,
            max_rounds=2,
            token_budget=20000,
            tool_call_budget=16,
            wall_clock_budget_s=120,
            stream_events=True,
        ),
    )

    await agent.handle(
        InboundMessage(
            text="research and compare alpha skill beta skill",
            sender_id="u",
            channel="cli",
            conversation_id="c-research",
        )
    )

    worker_one = "\n".join(llm.role_payloads["worker-1"])
    worker_two = "\n".join(llm.role_payloads["worker-2"])
    assert "alpha-skill" in worker_one
    assert "beta-skill" not in worker_one
    assert "beta-skill" in worker_two
    assert "alpha-skill" not in worker_two


def test_swarm_tool_permissions_respect_role_and_mode(tmp_path: Path, memory: MemoryStore):
    registry = _registry_with_skills(tmp_path, "greet")
    external = ToolRegistry()
    external.register(
        Tool(
            name="run_shell",
            description="shell",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
    )
    agent = AgentCore(
        llm=RoleAwareLLM(),
        skill_registry=registry,
        memory=memory,
        tool_registry=external,
        harness_config=SimpleNamespace(strategy="swarm"),
    )

    execute_tools = agent._swarm_tool_registries("task-1", "c1", None, "execute")
    assert "run_shell" in execute_tools["worker"].names()
    assert "set_fact" in execute_tools["worker"].names()
    assert "run_shell" not in execute_tools["planner"].names()
    assert "set_fact" not in execute_tools["critic"].names()

    plan_tools = agent._swarm_tool_registries("task-1", "c1", None, "plan")
    assert "run_shell" not in plan_tools["worker"].names()
    assert "set_fact" not in plan_tools["worker"].names()


def test_iteration_harness_rewrites_skill(tmp_path: Path, memory: MemoryStore):
    skill_path = _write(
        tmp_path / "skills" / "writer" / "SKILL.md",
        "---\nname: writer\ndescription: Write\noutcome_contract:\n  entity: article\n  primary_kpi: reads\n---\n"
        "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("writer")

    result = SimpleNamespace(
        top_performers=[
            SimpleNamespace(title="A", metrics={"reads": 100}),
            SimpleNamespace(title="B", metrics={"reads": 90}),
        ],
        patterns=["Use concrete examples"],
    )
    feedback = SkillFeedbackSummary(
        signal_confidence=0.8,
        signal_samples=3,
        has_strong_signal=True,
        average_score=0.9,
        score_samples=2,
        combined_confidence=0.8,
        should_rewrite=True,
        should_disable=False,
        evidence={"average_score": 0.9},
    )

    workflow = IterationHarness(memory)
    decision = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            feedback=feedback,
            result=result,
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert decision.decision == "rewrite"
    assert decision.skill_status == "active"
    updated = skill_path.read_text(encoding="utf-8")
    assert "Outcome-Backed Adjustments" in updated
    assert "Auto-updated by evidune" in updated


def test_iteration_harness_rewrites_from_contract_evidence_without_metrics(
    tmp_path: Path, memory: MemoryStore
):
    skill_path = _write(
        tmp_path / "skills" / "triage" / "SKILL.md",
        "---\nname: triage\ndescription: Triage incidents\noutcome_contract:\n  entity: incident\n  primary_kpi: resolution_score\n---\n"
        "## Instructions\nDiagnose with evidence.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("triage")
    memory.upsert_skill_evaluation_contract(
        "triage",
        {
            "version": 1,
            "criteria": [
                {"name": "goal_completion", "description": "Triage completed", "weight": 1.0}
            ],
            "observable_metrics": [],
            "failure_modes": ["skipped_required_verification"],
            "min_pass_score": 0.7,
            "rewrite_below_score": 0.55,
            "disable_below_score": 0.25,
            "min_samples_for_rewrite": 3,
            "min_samples_for_disable": 2,
        },
    )
    for score in [0.4, 0.5, 0.52]:
        execution_id = memory.record_execution(
            skill_name="triage",
            user_input="incident",
            assistant_output="restart",
        )
        memory.record_skill_evaluation(
            execution_id=execution_id,
            skill_name="triage",
            aggregate_score=score,
            criteria_scores={"goal_completion": score},
            missing_observations=["tool trace"],
            reasoning="Under-verified",
        )

    workflow = IterationHarness(memory)
    decision = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            result=SimpleNamespace(top_performers=[], patterns=[]),
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert decision.decision == "rewrite"
    updated = skill_path.read_text(encoding="utf-8")
    assert "Execution Contract Evidence" in updated
    assert "Average score" in updated


def test_iteration_harness_disables_from_contract_threshold(tmp_path: Path, memory: MemoryStore):
    skill_path = _write(
        tmp_path / "skills" / "emerged-low" / "SKILL.md",
        "---\nname: emerged-low\ndescription: Low scorer\noutcome_contract:\n  entity: task\n  primary_kpi: success_score\n---\n"
        "## Instructions\nHelp.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("emerged-low")
    memory.register_emerged_skill(name="emerged-low", status="active", path=str(skill_path))
    memory.upsert_skill_evaluation_contract(
        "emerged-low",
        {
            "version": 1,
            "criteria": [
                {"name": "goal_completion", "description": "Goal completed", "weight": 1.0}
            ],
            "observable_metrics": [],
            "failure_modes": [],
            "min_pass_score": 0.7,
            "rewrite_below_score": 0.55,
            "disable_below_score": 0.25,
            "min_samples_for_rewrite": 3,
            "min_samples_for_disable": 2,
        },
    )
    for score in [0.2, 0.22]:
        execution_id = memory.record_execution(
            skill_name="emerged-low",
            user_input="help",
            assistant_output="generic",
        )
        memory.record_skill_evaluation(
            execution_id=execution_id,
            skill_name="emerged-low",
            aggregate_score=score,
            criteria_scores={"goal_completion": score},
            reasoning="Fails the goal",
        )

    decision = IterationHarness(memory).run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            result=SimpleNamespace(top_performers=[], patterns=[]),
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert decision.decision == "disable"
    assert memory.get_emerged_skill("emerged-low")["status"] == "disabled"
    assert memory.get_skill_state("emerged-low")["status"] == "disabled"


def test_iteration_harness_disables_negative_emerged_skill(tmp_path: Path, memory: MemoryStore):
    skill_path = _write(
        tmp_path / "skills" / "emerged" / "SKILL.md",
        "---\nname: emerged\ndescription: Emerged\noutcome_contract:\n  entity: task\n  primary_kpi: success_score\n---\n"
        "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("emerged")
    memory.register_emerged_skill(name="emerged", status="active", path=str(skill_path))

    result = SimpleNamespace(top_performers=[], patterns=[])
    feedback = SkillFeedbackSummary(
        signal_confidence=-0.9,
        signal_samples=2,
        has_strong_signal=True,
        average_score=0.1,
        score_samples=1,
        combined_confidence=-0.9,
        should_rewrite=False,
        should_disable=True,
        evidence={"average_score": 0.1},
    )

    workflow = IterationHarness(memory)
    decision = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            feedback=feedback,
            result=result,
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert decision.decision == "disable"
    assert decision.skill_status == "disabled"
    assert memory.get_emerged_skill("emerged")["status"] == "disabled"
    assert memory.get_skill_state("emerged")["status"] == "disabled"


def test_iteration_harness_disables_base_skill_without_rewrite_history(
    tmp_path: Path, memory: MemoryStore
):
    skill_path = _write(
        tmp_path / "skills" / "base-writer" / "SKILL.md",
        "---\nname: base-writer\ndescription: Base\noutcome_contract:\n  entity: article\n  primary_kpi: reads\n---\n"
        "## Instructions\nWrite carefully.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("base-writer")
    memory.upsert_skill_state(
        "base-writer",
        origin="base",
        path=str(skill_path),
        status="active",
    )

    result = SimpleNamespace(top_performers=[], patterns=[])
    feedback = SkillFeedbackSummary(
        signal_confidence=-0.8,
        signal_samples=3,
        has_strong_signal=True,
        average_score=0.1,
        score_samples=2,
        combined_confidence=-0.8,
        should_rewrite=False,
        should_disable=True,
        evidence={"average_score": 0.1},
    )

    workflow = IterationHarness(memory)
    before = skill_path.read_text(encoding="utf-8")
    decision = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=before,
            feedback=feedback,
            result=result,
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert decision.decision == "disable"
    assert decision.skill_status == "disabled"
    assert decision.update.has_changes is False
    assert skill_path.read_text(encoding="utf-8") == before
    assert memory.get_skill_state("base-writer")["status"] == "disabled"


def test_iteration_harness_rolls_back_after_negative_feedback_on_rewritten_skill(
    tmp_path: Path, memory: MemoryStore
):
    skill_path = _write(
        tmp_path / "skills" / "writer" / "SKILL.md",
        "---\nname: writer\ndescription: Write\noutcome_contract:\n  entity: article\n  primary_kpi: reads\n---\n"
        "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("writer")
    positive = SimpleNamespace(
        top_performers=[
            SimpleNamespace(title="A", metrics={"reads": 100}),
            SimpleNamespace(title="B", metrics={"reads": 90}),
        ],
        patterns=["Use concrete examples"],
    )
    positive_feedback = SkillFeedbackSummary(
        signal_confidence=0.8,
        signal_samples=3,
        has_strong_signal=True,
        average_score=0.9,
        score_samples=2,
        combined_confidence=0.8,
        should_rewrite=True,
        should_disable=False,
        evidence={"average_score": 0.9},
    )
    workflow = IterationHarness(memory)
    first = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            feedback=positive_feedback,
            result=positive,
            surface="run",
            task_kind="skill_iteration",
        )
    )

    negative = SkillFeedbackSummary(
        signal_confidence=-0.8,
        signal_samples=3,
        has_strong_signal=True,
        average_score=0.1,
        score_samples=2,
        combined_confidence=-0.8,
        should_rewrite=False,
        should_disable=True,
        evidence={"average_score": 0.1},
    )
    second = workflow.run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            feedback=negative,
            result=positive,
            surface="run",
            task_kind="skill_iteration",
        )
    )

    assert first.decision == "rewrite"
    assert second.decision == "rollback"
    assert second.skill_status == "rolled_back"
    assert memory.get_skill_state("writer")["status"] == "rolled_back"
