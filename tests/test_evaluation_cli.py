from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.evaluation_cli import _promote, _rollback, _stage_candidate_from_evaluation, _variants
from memory.store import MemoryStore
from skills.governance import text_digest


def test_eval_without_candidate_does_not_execute_parent_twice(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: sample\ndescription: sample\nversion: 1\n---\nParent\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        skill_path=str(skill_path),
        with_baseline=False,
        experiment_id="",
        candidate_path="",
        mutation=[],
        skill_name="",
    )

    skill_name, variants = _variants(args, tmp_path, store)

    assert skill_name == "sample"
    assert [variant.name for variant in variants] == ["parent"]
    store.close()


def test_candidate_validation_mutates_candidate_not_parent(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    skill_path = tmp_path / "SKILL.md"
    parent = (
        "---\nname: sample\ndescription: sample\nversion: 1\n---\n"
        "## Instructions\nVerify parent.\n"
    )
    candidate = (
        "---\nname: sample\ndescription: sample\nversion: 2-candidate\n---\n"
        "## Instructions\nVerify candidate marker.\n"
    )
    skill_path.write_text(parent, encoding="utf-8")
    experiment_id = store.create_skill_experiment(
        skill_name="sample",
        parent_version="1",
        parent_digest=text_digest(parent),
        parent_content=parent,
        candidate_version="2-candidate",
        candidate_digest=text_digest(candidate),
        candidate_content=candidate,
        source_execution_ids=[],
    )
    args = SimpleNamespace(
        skill_path=str(skill_path),
        with_baseline=False,
        experiment_id=experiment_id,
        candidate_path="",
        mutation=["remove_verification"],
        skill_name="",
    )

    _, variants = _variants(args, tmp_path, store)

    mutation = next(variant for variant in variants if variant.mutation_operator)
    assert "parent" not in mutation.content
    assert "candidate marker" not in mutation.content
    assert mutation.version.startswith("2-candidate-mutant-")
    store.close()


@pytest.mark.asyncio
async def test_eval_failures_can_stage_candidate_without_changing_active_skill(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    skill_path = tmp_path / "SKILL.md"
    parent = (
        "---\nname: sample\ndescription: sample\nversion: 1\n---\n"
        "## Instructions\nVerify the requested state.\n"
    )
    skill_path.write_text(parent, encoding="utf-8")
    for index in range(3):
        execution_id = store.record_execution(
            skill_name="sample",
            skill_version="1",
            user_input=f"task {index}",
            assistant_output="incomplete",
        )
        store.record_evaluation_result(
            {
                "execution_id": execution_id,
                "skill_name": "sample",
                "skill_version": "1",
                "evaluator_id": "state",
                "evaluator_revision": "v1",
                "evaluator_type": "state_diff",
                "verdict": "fail",
                "failure_modes": ["incomplete_state"],
                "attribution_grade": "direct",
            }
        )
    args = SimpleNamespace(skill_path=str(skill_path))

    result = await _stage_candidate_from_evaluation(
        args=args,
        base_dir=tmp_path,
        memory=store,
        llm=None,
    )

    assert result["decision"] == "rewrite"
    assert result["skill_status"] == "candidate"
    assert result["candidate_experiment_id"].startswith("exp_")
    assert result["active_skill_changed"] is False
    assert skill_path.read_text(encoding="utf-8") == parent
    store.close()


def test_promote_and_rollback_are_digest_guarded(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    skill_path = tmp_path / "SKILL.md"
    parent = "---\nname: sample\ndescription: sample\nversion: 1\n---\nParent\n"
    candidate = "---\nname: sample\ndescription: sample\nversion: 2-candidate\n---\nCandidate\n"
    skill_path.write_text(parent, encoding="utf-8")
    source_execution_id = store.record_execution(
        skill_name="sample",
        skill_version="1",
        user_input="test",
        assistant_output="failed",
    )
    experiment_id = store.create_skill_experiment(
        skill_name="sample",
        parent_version="1",
        parent_digest=text_digest(parent),
        parent_content=parent,
        candidate_version="2-candidate",
        candidate_digest=text_digest(candidate),
        candidate_content=candidate,
        source_execution_ids=[source_execution_id],
        corpus_id="holdout",
        split="holdout",
    )
    store.transition_skill_experiment(
        experiment_id,
        "validated",
        evidence={"governance": {"promotable": True, "verdict": "pass"}},
    )
    args = SimpleNamespace(
        experiment_id=experiment_id,
        skill_path=str(skill_path),
        reason="test rollback",
    )

    assert _promote(store, tmp_path, args) == 0
    assert skill_path.read_text(encoding="utf-8") == candidate
    assert store.get_skill_experiment(experiment_id)["status"] == "promoted"

    assert _rollback(store, tmp_path, args) == 0
    assert skill_path.read_text(encoding="utf-8") == parent
    assert store.get_skill_experiment(experiment_id)["status"] == "rolled_back"

    live_required_id = store.create_skill_experiment(
        skill_name="sample",
        parent_version="1",
        parent_digest=text_digest(parent),
        parent_content=parent,
        candidate_version="3-candidate",
        candidate_digest=text_digest(candidate),
        candidate_content=candidate,
        source_execution_ids=[source_execution_id],
        corpus_id="holdout",
        split="holdout",
        model_ref={"provider": "fixture", "model": "deterministic"},
        policy={"require_live_model": True},
    )
    store.transition_skill_experiment(
        live_required_id,
        "validated",
        evidence={"governance": {"promotable": True, "verdict": "pass"}},
    )
    args.experiment_id = live_required_id
    with pytest.raises(ValueError, match="live model provider"):
        _promote(store, tmp_path, args)
    assert skill_path.read_text(encoding="utf-8") == parent
    store.close()
