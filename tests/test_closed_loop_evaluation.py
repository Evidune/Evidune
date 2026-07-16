"""One deterministic proof of failure -> candidate -> holdout -> promotion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.benchmark import BenchmarkExecution, load_evaluation_corpus
from agent.iteration_harness import IterationHarness, build_decision_packet
from core.evaluation_cli import _promote
from core.evaluation_runner import EvaluationExperimentRunner, VariantSpec
from memory.store import MemoryStore
from skills.governance import EvaluationResult, text_digest
from skills.loader import parse_skill


@pytest.mark.asyncio
async def test_execution_failures_produce_validated_promotable_candidate(tmp_path):
    skill_path = tmp_path / "skills" / "verifier" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    parent_content = (
        "---\nname: verifier\ndescription: Verify records\nversion: 1.0.0\n---\n\n"
        "## Instructions\n\nProcess the records.\n"
    )
    skill_path.write_text(parent_content, encoding="utf-8")
    store = MemoryStore(tmp_path / "memory.db")
    contract_digest = store.record_contract_snapshot(
        contract_kind="execution",
        contract={"required": ["verification"]},
        contract_version="v1",
    )
    source_ids = []
    for index in range(3):
        execution_id = store.record_execution(
            skill_name="verifier",
            skill_version="1.0.0",
            skill_digest=text_digest(parent_content),
            user_input=f"verify records {index}",
            assistant_output="processed without verification",
            execution_contract_digest=contract_digest,
        )
        source_ids.append(execution_id)
        store.record_evaluation_result(
            EvaluationResult(
                execution_id=execution_id,
                skill_name="verifier",
                skill_version="1.0.0",
                evaluator_id="trace_verification",
                evaluator_revision="v1",
                evaluator_type="trace",
                contract_digest=contract_digest,
                verdict="fail",
                failure_modes=["skipped_verification"],
                attribution_grade="direct",
            ).to_dict()
        )

    skill = parse_skill(skill_path)
    decision = IterationHarness(store).run(
        packet=build_decision_packet(
            store,
            skill=skill,
            current=parent_content,
            surface="eval",
            task_kind="closed_loop_test",
        )
    )
    assert decision.decision == "rewrite"
    assert decision.skill_status == "candidate"
    assert skill_path.read_text(encoding="utf-8") == parent_content
    candidate = store.list_skill_experiments("verifier", status="candidate")[0]
    assert candidate["source_execution_ids"] == source_ids
    assert "skipped_verification" in candidate["candidate_content"]

    manifest = tmp_path / "holdout.yaml"
    manifest.write_text(
        """corpus_id: verifier-holdout-v1
schema_version: 1
adapter: {id: fixture, revision: v1}
sources:
  - {name: verifier, url: "https://example.test/verifier", commit: abc, license: MIT}
tasks:
  - id: hidden-verification
    instruction: Process and verify the records
    initial_state: {verified: false}
    expected_state: {verified: true}
    metadata: {required_output_contains: [verified]}
splits: {holdout: [hidden-verification]}
environment: {kind: fixture}
evaluator:
  holdout_visibility: hidden
  required_evaluators: [fixture_state_and_output]
  required_mutations: [remove_verification]
  minimum_attribution: direct
budget: {max_model_calls: 3}
""",
        encoding="utf-8",
    )

    async def executor(prepared, skill_content, model_ref, trial):
        repaired = "skipped_verification" in skill_content and "MUTATED" not in skill_content
        return BenchmarkExecution(
            output="verified" if repaired else "not verified",
            final_state={"verified": repaired},
            tool_trace=[{"name": "verify", "performed": repaired}],
        )

    summary = await EvaluationExperimentRunner(store, base_dir=tmp_path).run(
        corpus=load_evaluation_corpus(manifest),
        split="holdout",
        variants=[
            VariantSpec("parent", "1.0.0", parent_content),
            VariantSpec(
                "candidate",
                candidate["candidate_version"],
                candidate["candidate_content"],
            ),
            VariantSpec(
                "mutation-remove-verification",
                "1.0.0-mutant",
                "MUTATED",
                "remove_verification",
            ),
        ],
        trials=1,
        executor=executor,
        model_ref={"provider": "fixture", "model": "deterministic"},
        skill_name="verifier",
        experiment_id=candidate["id"],
    )
    assert summary.status == "validated"
    assert summary.variant_counts["parent"]["fail"] == 1
    assert summary.variant_counts["mutation-remove-verification"]["fail"] == 1
    assert summary.variant_counts["candidate"]["pass"] == 1

    args = SimpleNamespace(
        experiment_id=candidate["id"],
        skill_path=str(skill_path),
        reason="",
    )
    assert _promote(store, tmp_path, args) == 0
    assert skill_path.read_text(encoding="utf-8") == candidate["candidate_content"]
    promoted = store.get_skill_experiment(candidate["id"])
    assert promoted["status"] == "promoted"
    assert promoted["source_execution_ids"] == source_ids
    store.close()
