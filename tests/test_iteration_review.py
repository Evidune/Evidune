"""Tests for the iteration proposal review checks and their harness wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.iteration_harness import IterationDecisionPacket, IterationHarness, build_decision_packet
from agent.iteration_review import review_proposal, split_frontmatter
from agent.skill_feedback import SkillFeedbackSummary
from memory.store import MemoryStore
from skills.registry import SkillRegistry

FRONTMATTER = "---\nname: writer\ndescription: Write\n---\n"
CURRENT = (
    FRONTMATTER + "## Instructions\n\nWrite helpful content.\n\n## Reference Data\n\nplaceholder\n"
)
VALID_REWRITE = (
    FRONTMATTER + "## Instructions\n\nWrite helpful content.\n\n### Outcome-Backed Adjustments\n\n"
    "- Reinforce this observed pattern: use examples.\n\n## Reference Data\n\nevidence\n"
)


def _packet(**overrides) -> IterationDecisionPacket:
    defaults = dict(
        skill_name="writer",
        skill_path="/tmp/SKILL.md",
        skill_origin="base",
        current_content=CURRENT,
    )
    defaults.update(overrides)
    return IterationDecisionPacket(**defaults)


class TestReviewProposal:
    def test_keep_and_disable_pass_without_checks(self):
        for decision in ("keep", "disable"):
            result = review_proposal(decision, _packet(), "")
            assert result.approved is True
            assert result.checks == {}
            assert result.reasons == []

    def test_valid_rewrite_passes_all_checks(self):
        result = review_proposal("rewrite", _packet(), VALID_REWRITE)
        assert result.approved is True
        assert all(result.checks.values())
        assert result.reasons == []

    def test_rewrite_copying_source_task_phrase_is_rejected(self):
        task = "Forward the quarterly planning message to the exact specified recipient today"
        proposal = VALID_REWRITE.replace(
            "- Reinforce this observed pattern: use examples.",
            f"- Always {task}.",
        )
        packet = _packet(executions=[{"user_input": task}])

        result = review_proposal("rewrite", packet, proposal)

        assert result.approved is False
        assert result.checks["source_task_not_copied"] is False

    def test_rewrite_repeating_rejected_candidate_body_is_rejected(self):
        rejected = VALID_REWRITE.replace(
            "name: writer",
            "name: writer\nversion: rejected-candidate",
        )
        packet = _packet(experiment_history=[{"status": "rejected", "candidate_content": rejected}])

        result = review_proposal("rewrite", packet, VALID_REWRITE)

        assert result.approved is False
        assert result.checks["not_duplicate_rejected_candidate"] is False

    def test_empty_proposal_is_rejected(self):
        result = review_proposal("rewrite", _packet(), "   \n")
        assert result.approved is False
        assert result.checks["non_empty"] is False
        assert result.reasons

    def test_frontmatter_whitespace_only_difference_is_accepted(self):
        # The frontmatter prefix absorbs blank lines after the closing ---;
        # a proposal dropping that blank line keeps the YAML byte-identical.
        current = (
            FRONTMATTER
            + "\n## Instructions\n\nWrite helpful content.\n\n## Reference Data\n\nplaceholder\n"
        )
        result = review_proposal("rewrite", _packet(current_content=current), VALID_REWRITE)
        assert result.checks["frontmatter_preserved"] is True
        assert result.approved is True

    def test_altered_frontmatter_is_rejected(self):
        tampered = VALID_REWRITE.replace("name: writer", "name: hijacked")
        result = review_proposal("rewrite", _packet(), tampered)
        assert result.approved is False
        assert result.checks["frontmatter_preserved"] is False

    def test_removed_instructions_section_is_rejected(self):
        proposal = FRONTMATTER + "## Reference Data\n\nevidence only\n"
        result = review_proposal("refresh", _packet(), proposal)
        assert result.approved is False
        assert result.checks["instructions_present"] is False

    def test_rewrite_deleting_manual_instructions_is_rejected(self):
        proposal = (
            FRONTMATTER
            + "## Instructions\n\n### Outcome-Backed Adjustments\n\n- machine notes only\n\n"
            "## Reference Data\n\nevidence\n"
        )
        result = review_proposal("rewrite", _packet(), proposal)
        assert result.approved is False
        assert result.checks["manual_instructions_preserved"] is False

    def test_rewrite_keeps_manual_base_across_prior_adjustments(self):
        packet = _packet(current_content=VALID_REWRITE)
        second_pass = VALID_REWRITE.replace("use examples", "use concrete data")
        result = review_proposal("rewrite", packet, second_pass)
        assert result.checks["manual_instructions_preserved"] is True

    def test_truncated_proposal_fails_size_check(self):
        packet = _packet(current_content=CURRENT + ("x" * 4000))
        result = review_proposal("refresh", packet, CURRENT)
        assert result.approved is False
        assert result.checks["size_sane"] is False

    def test_runaway_growth_fails_size_check(self):
        packet = _packet(current_content=CURRENT + ("x" * 4000))
        proposal = VALID_REWRITE + ("y" * 40000)
        result = review_proposal("rewrite", packet, proposal)
        assert result.approved is False
        assert result.checks["size_sane"] is False

    def test_small_skill_may_grow_past_three_x(self):
        # First evidence sections legitimately triple a tiny skill file.
        proposal = VALID_REWRITE + ("- More evidence lines.\n" * 20)
        assert len(proposal) > 3 * len(CURRENT)
        result = review_proposal("rewrite", _packet(), proposal)
        assert result.checks["size_sane"] is True

    def test_rollback_matching_recorded_content_passes(self):
        packet = _packet(
            current_content=VALID_REWRITE,
            lifecycle_history=[{"action": "rewrite", "content_before": CURRENT}],
        )
        proposal = FRONTMATTER + (
            "## Instructions\n\nWrite helpful content.\n\n## Reference Data\n\nnew evidence\n"
        )
        result = review_proposal("rollback", packet, proposal)
        assert result.approved is True
        assert result.checks["rollback_matches_history"] is True

    def test_rollback_diverging_from_recorded_content_is_rejected(self):
        packet = _packet(
            current_content=VALID_REWRITE,
            lifecycle_history=[{"action": "rewrite", "content_before": CURRENT}],
        )
        proposal = FRONTMATTER + (
            "## Instructions\n\nEntirely new instructions.\n\n## Reference Data\n\ndata\n"
        )
        result = review_proposal("rollback", packet, proposal)
        assert result.approved is False
        assert result.checks["rollback_matches_history"] is False

    def test_rollback_without_recorded_rewrite_is_rejected(self):
        result = review_proposal("rollback", _packet(), CURRENT)
        assert result.approved is False
        assert result.checks["rollback_matches_history"] is False


class _ManualDeletingHarness(IterationHarness):
    """Forces a rewrite proposal that drops the manual instructions."""

    def _propose(self, packet: IterationDecisionPacket) -> tuple[str, str, dict]:
        prefix, _body = split_frontmatter(packet.current_content)
        return (
            "rewrite",
            prefix + "## Instructions\n\n### Outcome-Backed Adjustments\n\n- machine notes only\n\n"
            "## Reference Data\n\nevidence\n",
            {},
        )


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    yield store
    store.close()


def test_harness_downgrades_rejected_rewrite_to_keep(tmp_path: Path, memory: MemoryStore):
    skill_path = tmp_path / "skills" / "writer" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        "---\nname: writer\ndescription: Write\noutcome_contract:\n  entity: article\n  primary_kpi: reads\n---\n"
        "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.load_directory(tmp_path / "skills")
    skill = registry.get("writer")
    before = skill_path.read_text(encoding="utf-8")

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

    decision = _ManualDeletingHarness(memory).run(
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

    assert decision.decision == "keep"
    assert decision.update.has_changes is False
    assert skill_path.read_text(encoding="utf-8") == before

    artifacts = memory.list_harness_artifacts(decision.task.id)
    review = next(item for item in artifacts if item["kind"] == "review")
    assert review["accepted"] is False
    assert review["content"] == "rejected"
    assert review["meta"]["checks"]["manual_instructions_preserved"] is False
    assert any("manual instructions" in reason for reason in review["meta"]["reasons"])
    assert "manual instructions" in review["summary"]
