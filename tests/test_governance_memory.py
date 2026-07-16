"""Storage contract tests for execution-grounded Skill evidence."""

import sqlite3
from pathlib import Path

import pytest

from memory.schema import init_schema
from memory.store import MemoryStore
from skills.governance import EvaluationResult, text_digest


@pytest.fixture
def store(tmp_path: Path):
    memory = MemoryStore(tmp_path / "governance.db")
    yield memory
    memory.close()


def _execution(store: MemoryStore) -> int:
    return store.record_execution(
        skill_name="writer",
        skill_version="2.0.0",
        skill_digest=text_digest("skill v2"),
        user_input="write",
        assistant_output="done",
        tool_trace=[{"name": "save", "result": "ok"}],
        artifact_refs=["artifact://result.md"],
        model_ref={"provider": "test", "model": "fixture"},
    )


def test_execution_lineage_and_immutable_contract_snapshot(store: MemoryStore):
    contract = {"version": 2, "criteria": [{"name": "done"}]}
    digest = store.record_contract_snapshot(
        contract_kind="execution",
        contract=contract,
        contract_version="2",
        source="test",
    )
    execution_id = store.record_execution(
        skill_name="writer",
        skill_version="2.0.0",
        skill_digest=text_digest("skill v2"),
        execution_contract_digest=digest,
        user_input="write",
        assistant_output="done",
        tool_trace=[{"name": "save"}],
    )

    execution = store.get_skill_executions_by_id(execution_id)
    assert execution["execution_uid"].startswith("exe_")
    assert execution["skill_version"] == "2.0.0"
    assert execution["execution_contract_digest"] == digest
    assert execution["tool_trace"] == [{"name": "save"}]
    assert store.get_contract_snapshot(digest)["contract"] == contract

    with pytest.raises(ValueError, match="immutable snapshot"):
        store.record_contract_snapshot(
            contract_kind="execution",
            contract={"version": 999},
            digest=digest,
        )


def test_typed_result_keeps_native_verdict_without_score(store: MemoryStore):
    execution_id = _execution(store)
    result = EvaluationResult(
        execution_id=execution_id,
        skill_name="writer",
        skill_version="2.0.0",
        evaluator_id="state",
        evaluator_revision="v1",
        evaluator_type="state_diff",
        verdict="pass",
        dimensions={"expected_state_reached": True},
        attribution_grade="direct",
    )

    result_uid = store.record_evaluation_result(result.to_dict())
    saved = store.list_evaluation_results(execution_id=execution_id)

    assert result_uid.startswith("evr_")
    assert saved[0]["score"] is None
    assert saved[0]["verdict"] == "pass"
    assert saved[0]["dimensions"]["expected_state_reached"] is True


def test_typed_result_cannot_claim_another_skill_version(store: MemoryStore):
    execution_id = _execution(store)

    with pytest.raises(ValueError, match="version does not match"):
        store.record_evaluation_result(
            EvaluationResult(
                execution_id=execution_id,
                skill_name="writer",
                skill_version="3.0.0",
                evaluator_id="state",
                evaluator_revision="v1",
                evaluator_type="state_diff",
                verdict="pass",
            ).to_dict()
        )


def test_binding_lifecycle_and_observation_idempotency(store: MemoryStore):
    execution_id = _execution(store)
    binding_id = store.create_evidence_binding(
        execution_id=execution_id,
        skill_name="writer",
        skill_version="2.0.0",
        entity_type="document",
        entity_id="doc-1",
        observation_plan={"horizons": [{"id": "immediate"}]},
        attribution_policy="direct",
        minimum_evidence_grade="direct",
    )

    store.transition_evidence_binding(binding_id, "scheduled")
    store.transition_evidence_binding(binding_id, "observing")
    store.record_probe_attempt(
        binding_id=binding_id,
        horizon_id="immediate",
        probe_revision="v1",
        status="completed",
        payload={"exists": True},
    )
    first = store.record_evidence_observation(
        binding_id=binding_id,
        horizon_id="immediate",
        probe_revision="v1",
        observation_kind="state",
        payload={"exists": True},
        observed_at="2026-07-15T00:00:00+00:00",
    )
    duplicate = store.record_evidence_observation(
        binding_id=binding_id,
        horizon_id="immediate",
        probe_revision="v1",
        observation_kind="state",
        payload={"exists": True},
        observed_at="2026-07-15T00:00:00+00:00",
    )
    store.transition_evidence_binding(binding_id, "evaluated")

    assert first > 0
    assert duplicate == 0
    assert store.get_evidence_binding(binding_id)["status"] == "evaluated"
    with pytest.raises(ValueError, match="Invalid evidence binding transition"):
        store.transition_evidence_binding(binding_id, "observing")


def test_binding_cannot_claim_another_skill_version(store: MemoryStore):
    execution_id = _execution(store)

    with pytest.raises(ValueError, match="version does not match"):
        store.create_evidence_binding(
            execution_id=execution_id,
            skill_name="writer",
            skill_version="3.0.0",
            entity_type="document",
            entity_id="doc-1",
            observation_plan={"horizons": [{"id": "immediate"}]},
        )


def test_candidate_experiment_has_explicit_lifecycle_and_trials(store: MemoryStore):
    source_execution_id = _execution(store)
    experiment_id = store.create_skill_experiment(
        skill_name="writer",
        parent_version="2.0.0",
        parent_digest=text_digest("parent"),
        parent_content="parent",
        candidate_version="2.0.1-candidate",
        candidate_digest=text_digest("candidate"),
        candidate_content="candidate",
        source_execution_ids=[source_execution_id],
        corpus_id="fixture-v1",
        split="development",
    )
    store.record_experiment_trial(
        experiment_id=experiment_id,
        task_ref="task-1",
        split="development",
        variant="candidate",
        trial_number=1,
        status="passed",
        execution_id=source_execution_id,
    )
    store.transition_skill_experiment(experiment_id, "validated", evidence={"gates": "pass"})
    store.transition_skill_experiment(experiment_id, "promoted")

    experiment = store.get_skill_experiment(experiment_id)
    assert experiment["status"] == "promoted"
    assert experiment["source_execution_ids"] == [source_execution_id]
    assert store.list_experiment_trials(experiment_id)[0]["variant"] == "candidate"

    with pytest.raises(ValueError, match="Invalid Skill experiment transition"):
        store.transition_skill_experiment(experiment_id, "candidate")


def test_candidate_experiment_accepts_same_skill_cross_version_learning(store: MemoryStore):
    source_execution_id = _execution(store)

    experiment_id = store.create_skill_experiment(
        skill_name="writer",
        parent_version="3.0.0",
        parent_digest=text_digest("parent"),
        parent_content="parent",
        candidate_version="3.0.1-candidate",
        candidate_digest=text_digest("candidate"),
        candidate_content="candidate",
        source_execution_ids=[source_execution_id],
    )

    assert store.get_skill_experiment(experiment_id)["source_execution_ids"] == [
        source_execution_id
    ]

    with pytest.raises(ValueError, match="Unknown source execution_id"):
        store.create_skill_experiment(
            skill_name="writer",
            parent_version="2.0.0",
            parent_digest=text_digest("parent"),
            parent_content="parent",
            candidate_version="2.0.1-candidate",
            candidate_digest=text_digest("candidate"),
            candidate_content="candidate",
            source_execution_ids=[999_999],
        )


def test_experiment_trial_rejects_unknown_execution(store: MemoryStore):
    source_execution_id = _execution(store)
    experiment_id = store.create_skill_experiment(
        skill_name="writer",
        parent_version="2.0.0",
        parent_digest=text_digest("parent"),
        parent_content="parent",
        candidate_version="2.0.1-candidate",
        candidate_digest=text_digest("candidate"),
        candidate_content="candidate",
        source_execution_ids=[source_execution_id],
    )

    with pytest.raises(ValueError, match="Unknown trial execution_id"):
        store.record_experiment_trial(
            experiment_id=experiment_id,
            task_ref="task-1",
            split="development",
            variant="candidate",
            trial_number=1,
            status="passed",
            execution_id=999_999,
        )


def test_legacy_execution_table_is_migrated(tmp_path: Path):
    connection = sqlite3.connect(tmp_path / "legacy.db")
    connection.executescript(
        """
        CREATE TABLE skill_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            conversation_id TEXT,
            user_input TEXT NOT NULL,
            assistant_output TEXT NOT NULL,
            signals_json TEXT DEFAULT '{}',
            cross_model_score REAL,
            evaluator_reasoning TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    init_schema(connection)

    columns = {row[1] for row in connection.execute("PRAGMA table_info(skill_executions)")}
    assert {"execution_uid", "skill_version", "skill_digest", "tool_trace_json"} <= columns
    connection.close()
