"""Tests for LLM-backed skill rewrite proposals and harness preference."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.iteration_harness import IterationDecisionPacket, IterationHarness, build_decision_packet
from agent.skill_feedback import SkillFeedbackSummary
from agent.skill_rewriter import propose_skill_rewrite
from memory.store import MemoryStore
from skills.registry import SkillRegistry

FRONTMATTER = (
    "---\nname: writer\ndescription: Write\noutcome_contract:\n"
    "  entity: article\n  primary_kpi: reads\n---\n"
)
CURRENT = (
    FRONTMATTER + "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n"
)
LLM_PROPOSAL = (
    FRONTMATTER + "## Instructions\n\nWrite helpful content.\n\n"
    "### Outcome-Backed Adjustments\n\n"
    "- Lead with a concrete hook drawn from exemplar A.\n\n"
    "## Reference Data\nplaceholder\n"
)


class StaticLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return self.response


class FailingLLM:
    async def complete(self, messages, **kwargs):
        raise RuntimeError("llm unavailable")


def _packet(current: str = CURRENT) -> IterationDecisionPacket:
    return IterationDecisionPacket(
        skill_name="writer",
        skill_path="/tmp/writer/SKILL.md",
        skill_origin="base",
        current_content=current,
        outcome_contract={"primary_kpi": "reads"},
        exemplar_slice=[{"exemplar": "A", "reads": 100.0}],
        regression_summary={"legacy_patterns": ["Use concrete examples"]},
    )


class TestProposeSkillRewrite:
    @pytest.mark.asyncio
    async def test_returns_full_proposal_and_includes_evidence_in_prompt(self):
        llm = StaticLLM(LLM_PROPOSAL)
        proposal = await propose_skill_rewrite(llm, _packet())
        assert proposal == LLM_PROPOSAL
        assert "reads" in llm.prompts[0]
        assert "Use concrete examples" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_accepts_fenced_markdown(self):
        llm = StaticLLM("```markdown\n" + LLM_PROPOSAL + "```")
        proposal = await propose_skill_rewrite(llm, _packet())
        assert proposal.startswith("---\n")
        assert "Outcome-Backed Adjustments" in proposal

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        assert await propose_skill_rewrite(FailingLLM(), _packet()) == ""

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        assert await propose_skill_rewrite(StaticLLM("   "), _packet()) == ""

    @pytest.mark.asyncio
    async def test_missing_llm_returns_empty(self):
        assert await propose_skill_rewrite(None, _packet()) == ""

    @pytest.mark.asyncio
    async def test_altered_frontmatter_is_rejected(self):
        tampered = LLM_PROPOSAL.replace("description: Write", "description: Changed")
        assert await propose_skill_rewrite(StaticLLM(tampered), _packet()) == ""

    @pytest.mark.asyncio
    async def test_dropped_manual_instructions_are_rejected(self):
        gutted = LLM_PROPOSAL.replace("Write helpful content.", "Do something else.")
        assert await propose_skill_rewrite(StaticLLM(gutted), _packet()) == ""


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    yield store
    store.close()


def _skill_setup(tmp_path: Path):
    skill_path = tmp_path / "skills" / "writer" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(CURRENT, encoding="utf-8")
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    return registry.get("writer"), skill_path


def _rewrite_inputs():
    result = SimpleNamespace(
        top_performers=[SimpleNamespace(title="A", metrics={"reads": 100})],
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
    return result, feedback


class TestHarnessLLMProposalPreference:
    def test_valid_llm_proposal_is_preferred_over_template(
        self, tmp_path: Path, memory: MemoryStore
    ):
        skill, skill_path = _skill_setup(tmp_path)
        result, feedback = _rewrite_inputs()
        packet = build_decision_packet(
            memory,
            skill=skill,
            current=CURRENT,
            feedback=feedback,
            result=result,
            surface="run",
        )
        packet.llm_rewrite_proposal = LLM_PROPOSAL

        decision = IterationHarness(memory).run(packet=packet)

        assert decision.decision == "rewrite"
        assert decision.skill_status == "observing"
        updated = skill_path.read_text(encoding="utf-8")
        assert "Lead with a concrete hook drawn from exemplar A." in updated
        assert "Auto-updated by evidune" in updated  # reference data still refreshed
        event = memory.get_latest_skill_lifecycle_event("writer", "rewrite")
        assert event["evidence"]["rewrite_source"] == "llm"

    def test_rejected_llm_proposal_falls_back_to_template(
        self, tmp_path: Path, memory: MemoryStore
    ):
        skill, skill_path = _skill_setup(tmp_path)
        result, feedback = _rewrite_inputs()
        packet = build_decision_packet(
            memory,
            skill=skill,
            current=CURRENT,
            feedback=feedback,
            result=result,
            surface="run",
        )
        packet.llm_rewrite_proposal = LLM_PROPOSAL.replace(
            "description: Write", "description: Changed"
        )

        decision = IterationHarness(memory).run(packet=packet)

        assert decision.decision == "rewrite"
        updated = skill_path.read_text(encoding="utf-8")
        assert "description: Changed" not in updated
        assert "### Outcome-Backed Adjustments" in updated
        event = memory.get_latest_skill_lifecycle_event("writer", "rewrite")
        assert event["evidence"]["rewrite_source"] == "template"

    def test_no_proposal_uses_template(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        result, feedback = _rewrite_inputs()
        packet = build_decision_packet(
            memory,
            skill=skill,
            current=CURRENT,
            feedback=feedback,
            result=result,
            surface="run",
        )

        decision = IterationHarness(memory).run(packet=packet)

        assert decision.decision == "rewrite"
        assert "### Outcome-Backed Adjustments" in skill_path.read_text(encoding="utf-8")
        event = memory.get_latest_skill_lifecycle_event("writer", "rewrite")
        assert event["evidence"]["rewrite_source"] == "template"
