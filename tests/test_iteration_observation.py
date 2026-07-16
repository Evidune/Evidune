"""Tests for the post-rewrite observation window (confirm / hold / rollback)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.iteration_harness import IterationHarness, build_decision_packet
from agent.iteration_observation import observation_outcome
from agent.skill_feedback import SkillFeedbackSummary
from memory.store import MemoryStore
from skills.registry import SkillRegistry

CURRENT = (
    "---\nname: writer\ndescription: Write\noutcome_contract:\n"
    "  entity: article\n  primary_kpi: reads\n---\n"
    "## Instructions\nWrite helpful content.\n\n## Reference Data\nplaceholder\n"
)


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


def _apply_first_rewrite(memory: MemoryStore, skill, skill_path: Path) -> None:
    """Seed a legacy in-place rewrite to preserve rollback compatibility coverage."""
    before = skill_path.read_text(encoding="utf-8")
    after = before.replace(
        "Write helpful content.",
        "Write helpful content.\n\n### Outcome-Backed Adjustments\n\n- Use concrete examples.",
    )
    skill_path.write_text(after, encoding="utf-8")
    memory.upsert_skill_state(
        "writer",
        origin="base",
        path=str(skill_path),
        status="observing",
        reason="legacy compatibility fixture",
    )
    memory.record_skill_lifecycle_event(
        "writer",
        "rewrite",
        status="observing",
        path=str(skill_path),
        reason="legacy compatibility fixture",
        content_before=before,
        content_after=after,
    )
    assert memory.get_skill_state("writer")["status"] == "observing"


def _record_evaluations(memory: MemoryStore, scores: list[float]) -> None:
    for score in scores:
        execution_id = memory.record_execution(
            skill_name="writer",
            user_input="write",
            assistant_output="draft",
        )
        memory.record_skill_evaluation(
            execution_id=execution_id,
            skill_name="writer",
            aggregate_score=score,
            criteria_scores={"goal_completion": score},
        )


def _second_run(memory: MemoryStore, skill, skill_path: Path):
    return IterationHarness(memory).run(
        packet=build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            result=None,
            surface="run",
        )
    )


class TestObservationWindow:
    def test_observe_then_confirm_on_good_scores(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        _record_evaluations(memory, [0.9, 0.85, 0.8])

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "confirm"
        assert decision.skill_status == "active"
        assert decision.update.has_changes is False
        assert memory.get_skill_state("writer")["status"] == "active"
        event = memory.get_latest_skill_lifecycle_event("writer", "confirm")
        assert event is not None
        assert event["evidence"]["observation"]["samples"] == 3
        # The confirmed rewrite content stays on disk.
        assert "### Outcome-Backed Adjustments" in skill_path.read_text(encoding="utf-8")

    def test_observe_then_auto_rollback_on_bad_scores(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        _record_evaluations(memory, [0.4, 0.35, 0.3])

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "rollback"
        assert decision.skill_status == "rolled_back"
        assert memory.get_skill_state("writer")["status"] == "rolled_back"
        restored = skill_path.read_text(encoding="utf-8")
        assert "Write helpful content." in restored
        assert "### Outcome-Backed Adjustments" not in restored
        event = memory.get_latest_skill_lifecycle_event("writer", "rollback")
        assert event["evidence"]["observation"]["samples"] == 3

    def test_observing_holds_without_enough_samples(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        _record_evaluations(memory, [0.9, 0.9])
        rewritten = skill_path.read_text(encoding="utf-8")

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "keep"
        assert decision.skill_status == "observing"
        assert skill_path.read_text(encoding="utf-8") == rewritten

    def test_no_new_rewrite_while_observing(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        rewritten = skill_path.read_text(encoding="utf-8")

        # Evidence that would normally trigger a rewrite must be held back
        # while the observation window is open.
        result = SimpleNamespace(
            top_performers=[SimpleNamespace(title="B", metrics={"reads": 200})],
            patterns=["Another pattern"],
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
        packet = build_decision_packet(
            memory,
            skill=skill,
            current=rewritten,
            feedback=feedback,
            result=result,
            surface="run",
        )

        assert workflow.rewrite_is_due(packet) is False
        decision = workflow.run(packet=packet)
        assert decision.decision == "keep"
        assert memory.get_skill_state("writer")["status"] == "observing"

    def test_confirmed_rewrite_not_retriggered_by_stale_evaluations(
        self, tmp_path: Path, memory: MemoryStore
    ):
        skill, skill_path = _skill_setup(tmp_path)
        _record_evaluations(memory, [0.3] * 6)  # pre-rewrite evidence
        _apply_first_rewrite(memory, skill, skill_path)
        _record_evaluations(memory, [0.9, 0.9, 0.9])
        assert _second_run(memory, skill, skill_path).decision == "confirm"

        # No new evidence after the confirm: the stale pre-rewrite scores must
        # not re-open a rewrite (or its serve-mode LLM proposal call).
        workflow = IterationHarness(memory)
        packet = build_decision_packet(
            memory,
            skill=skill,
            current=skill_path.read_text(encoding="utf-8"),
            result=None,
            surface="run",
        )
        assert workflow.rewrite_is_due(packet) is False
        decision = workflow.run(packet=packet)
        assert decision.decision in {"keep", "refresh"}
        assert memory.get_skill_state("writer")["status"] == "active"

    def test_observation_outcome_between_bounds_holds(self):
        packet = SimpleNamespace(
            execution_contract={
                "min_pass_score": 0.7,
                "rewrite_below_score": 0.55,
                "min_samples_for_rewrite": 3,
            },
            execution_evaluations=[
                {"aggregate_score": 0.6, "created_at": "2026-07-15T01:00:00+00:00"},
                {"aggregate_score": 0.65, "created_at": "2026-07-15T01:01:00+00:00"},
                {"aggregate_score": 0.6, "created_at": "2026-07-15T01:02:00+00:00"},
                # Recorded before the rewrite: must be excluded from the window.
                {"aggregate_score": 0.1, "created_at": "2026-07-14T00:00:00+00:00"},
            ],
        )
        outcome = observation_outcome(packet, {"created_at": "2026-07-15T00:00:00+00:00"})
        assert outcome.verdict == "hold"
        assert outcome.window["samples"] == 3
        assert outcome.window["average_score"] == pytest.approx(0.6166, abs=1e-3)


class TestRunOnlyObservationWindow:
    """evidune-run-only deployments never write skill_evaluations rows, so the
    window must resolve from post-rewrite outcome KPI windows instead of
    holding forever and freezing reference refreshes."""

    def _record_outcome_windows(
        self, memory: MemoryStore, count: int, policy_state: dict | None = None
    ) -> None:
        for index in range(count):
            memory.record_outcome_window_summary(
                skill_name="writer",
                primary_kpi="reads",
                summary={
                    "sample_count": 10,
                    "policy_state": policy_state if index == count - 1 else {},
                },
            )

    def test_confirms_via_outcome_windows(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        self._record_outcome_windows(memory, 3)

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "confirm"
        assert decision.skill_status == "active"
        assert memory.get_skill_state("writer")["status"] == "active"

    def test_rolls_back_on_regressing_outcome_windows(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        self._record_outcome_windows(memory, 3, policy_state={"rollback_candidate": True})

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "rollback"
        assert memory.get_skill_state("writer")["status"] == "rolled_back"
        assert "### Outcome-Backed Adjustments" not in skill_path.read_text(encoding="utf-8")

    def test_holds_until_enough_outcome_windows(self, tmp_path: Path, memory: MemoryStore):
        skill, skill_path = _skill_setup(tmp_path)
        _apply_first_rewrite(memory, skill, skill_path)
        self._record_outcome_windows(memory, 1)

        decision = _second_run(memory, skill, skill_path)

        assert decision.decision == "keep"
        assert memory.get_skill_state("writer")["status"] == "observing"
