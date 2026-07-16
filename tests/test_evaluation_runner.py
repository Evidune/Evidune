"""End-to-end local experiment, artifact, and replay tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from adapters.benchmark import BenchmarkExecution, load_evaluation_corpus
from core.evaluation_runner import EvaluationExperimentRunner, VariantSpec
from memory.store import MemoryStore


def _corpus(tmp_path: Path) -> Path:
    manifest = tmp_path / "corpus.yaml"
    manifest.write_text(
        """corpus_id: runner-fixture-v1
schema_version: 1
adapter: {id: fixture, revision: v1}
sources:
  - {name: local-skill, url: "https://example.test/skill", commit: abc, license: MIT}
tasks:
  - id: update
    instruction: Mark the work done and report success
    initial_state: {done: false}
    expected_state: {done: true}
    forbidden_state: {deleted: true}
    metadata:
      required_output_contains: [success]
splits: {development: [update], holdout: []}
environment: {kind: fixture}
evaluator:
  holdout_visibility: hidden
  required_evaluators: [fixture_state_and_output]
  minimum_attribution: direct
budget: {max_model_calls: 20}
""",
        encoding="utf-8",
    )
    return manifest


def _corpus_with_minimum_valid_trials(tmp_path: Path, minimum: int) -> Path:
    manifest = _corpus(tmp_path)
    content = manifest.read_text(encoding="utf-8").replace(
        "  minimum_attribution: direct\n",
        f"  minimum_attribution: direct\n  minimum_valid_trials: {minimum}\n",
    )
    manifest.write_text(content, encoding="utf-8")
    return manifest


async def _executor(prepared, skill_content, model_ref, trial):
    if "MUTATED" in skill_content:
        return BenchmarkExecution(
            output="failed",
            final_state={"done": False, "deleted": True},
            metadata={"trial": trial},
        )
    return BenchmarkExecution(
        output="success",
        final_state={"done": True},
        tool_trace=[{"name": "fixture_update", "result": "ok"}],
        metadata={"trial": trial, "model": model_ref.get("model")},
    )


@pytest.mark.asyncio
async def test_runner_persists_closed_loop_artifacts_and_replays(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[
            VariantSpec("parent", "1.0.0", "GOOD"),
            VariantSpec("candidate", "1.0.1-candidate", "GOOD IMPROVED"),
            VariantSpec("mutation-remove-check", "1.0.0-mutant", "MUTATED", "remove_check"),
        ],
        trials=2,
        executor=_executor,
        model_ref={"provider": "fixture", "model": "deterministic", "temperature": 0},
        skill_name="local-skill",
    )

    assert summary.status == "validated"
    assert summary.valid_trials == 6
    assert summary.variant_counts["candidate"]["pass"] == 2
    assert summary.variant_counts["mutation-remove-check"]["fail"] == 2
    artifact_dir = Path(summary.artifact_dir)
    assert (artifact_dir / "manifest.json").is_file()
    assert (artifact_dir / "summary.json").is_file()
    assert (artifact_dir / "summary.md").is_file()
    assert (artifact_dir / "junit.xml").is_file()

    experiment = store.get_skill_experiment(summary.experiment_id)
    assert experiment["status"] == "validated"
    assert len(store.list_experiment_trials(summary.experiment_id)) == 6
    candidate_executions = [
        execution
        for execution in store.get_skill_executions("local-skill", limit=20)
        if execution["variant"] == "candidate"
    ]
    assert candidate_executions[0]["corpus_id"] == "runner-fixture-v1"
    assert store.list_evaluation_results(execution_id=candidate_executions[0]["id"])

    replay = runner.replay(summary.experiment_id)
    assert replay.promotable is True
    assert (artifact_dir / "replay.json").is_file()
    store.close()


@pytest.mark.asyncio
async def test_invalid_provider_run_is_not_negative_skill_evidence(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))

    async def unavailable(*args):
        raise ConnectionError("provider unavailable")

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[VariantSpec("candidate", "1.0.1-candidate", "GOOD")],
        trials=3,
        executor=unavailable,
        model_ref={"provider": "fixture", "model": "offline"},
        skill_name="local-skill",
    )

    assert summary.status == "inconclusive"
    assert summary.invalid_trials == 1
    assert summary.early_stop["reason"] == "candidate_insufficient_valid_trials"
    assert summary.governance["verdict"] == "inconclusive"
    assert store.list_evaluation_results(skill_name="local-skill") == []
    trial = store.list_experiment_trials(summary.experiment_id)[0]
    assert trial["classification"] == "external_dependency_unavailable"
    assert len(store.list_experiment_trials(summary.experiment_id)) == 1

    retried = await runner.run(
        corpus=corpus,
        split="development",
        variants=[VariantSpec("candidate", "1.0.1-candidate", "GOOD")],
        trials=1,
        executor=_executor,
        model_ref={"provider": "fixture", "model": "recovered"},
        skill_name="local-skill",
        experiment_id=summary.experiment_id,
    )
    assert retried.status == "validated"
    assert retried.experiment_id != summary.experiment_id
    assert store.get_skill_experiment(summary.experiment_id)["status"] == "inconclusive"
    assert store.get_skill_experiment(retried.experiment_id)["policy"]["retry_of"] == (
        summary.experiment_id
    )
    store.close()


@pytest.mark.asyncio
async def test_extra_trial_allows_one_invalid_provider_attempt(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus_with_minimum_valid_trials(tmp_path, 2))
    attempts = 0

    async def flaky(prepared, skill_content, model_ref, trial):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("transient provider failure")
        return await _executor(prepared, skill_content, model_ref, trial)

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[VariantSpec("candidate", "1.0.1-candidate", "GOOD")],
        trials=3,
        executor=flaky,
        model_ref={"provider": "fixture", "model": "flaky"},
        skill_name="local-skill",
    )

    assert summary.status == "validated"
    assert summary.valid_trials == 2
    assert summary.invalid_trials == 1
    store.close()


@pytest.mark.asyncio
async def test_mutation_requires_minimum_valid_trials_for_each_task(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus_with_minimum_valid_trials(tmp_path, 2))
    mutation_attempts = 0

    async def flaky_mutation(prepared, skill_content, model_ref, trial):
        nonlocal mutation_attempts
        if "MUTATED" in skill_content:
            mutation_attempts += 1
            if mutation_attempts == 1:
                raise ConnectionError("transient mutation provider failure")
        return await _executor(prepared, skill_content, model_ref, trial)

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[
            VariantSpec("candidate", "1.0.1-candidate", "GOOD"),
            VariantSpec("mutation-remove-check", "1.0.1-mutant", "MUTATED", "remove_check"),
        ],
        trials=2,
        executor=flaky_mutation,
        model_ref={"provider": "fixture", "model": "flaky"},
        skill_name="local-skill",
    )

    assert summary.status == "inconclusive"
    assert summary.governance["missing_evaluators"] == ["valid_mutation_trials:remove_check:update"]
    store.close()


@pytest.mark.asyncio
async def test_runner_validates_an_existing_staged_candidate(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))
    parent = VariantSpec("parent", "1.0.0", "GOOD")
    candidate = VariantSpec("candidate", "1.0.1-candidate", "GOOD IMPROVED")
    experiment_id = store.create_skill_experiment(
        skill_name="local-skill",
        parent_version="1.0.0",
        parent_digest=parent.digest,
        parent_content="GOOD",
        candidate_version="1.0.1-candidate",
        candidate_digest=candidate.digest,
        candidate_content="GOOD IMPROVED",
        source_execution_ids=[],
    )

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[parent, candidate],
        trials=1,
        executor=_executor,
        model_ref={"provider": "fixture", "model": "deterministic"},
        skill_name="local-skill",
        experiment_id=experiment_id,
    )

    assert summary.experiment_id == experiment_id
    experiment = store.get_skill_experiment(experiment_id)
    assert experiment["status"] == "validated"
    assert experiment["corpus_id"] == corpus.corpus_id
    assert experiment["split"] == "development"
    store.close()


@pytest.mark.asyncio
async def test_runner_rejects_candidate_source_overlap_with_holdout(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = replace(
        load_evaluation_corpus(_corpus(tmp_path)),
        splits={"development": [], "holdout": ["update"]},
    )
    source_execution_id = store.record_execution(
        skill_name="local-skill",
        skill_version="1.0.0",
        corpus_id=corpus.corpus_id,
        benchmark_task_id="update",
        variant="parent",
        user_input="Mark the work done and report success",
        assistant_output="failed",
    )
    parent = VariantSpec("parent", "1.0.0", "GOOD")
    candidate = VariantSpec("candidate", "1.0.1-candidate", "GOOD IMPROVED")
    experiment_id = store.create_skill_experiment(
        skill_name="local-skill",
        parent_version=parent.version,
        parent_digest=parent.digest,
        parent_content=parent.content,
        candidate_version=candidate.version,
        candidate_digest=candidate.digest,
        candidate_content=candidate.content,
        source_execution_ids=[source_execution_id],
    )

    with pytest.raises(ValueError, match="overlap holdout tasks: update"):
        await runner.run(
            corpus=corpus,
            split="holdout",
            variants=[parent, candidate],
            trials=1,
            executor=_executor,
            model_ref={"provider": "fixture", "model": "deterministic"},
            skill_name="local-skill",
            experiment_id=experiment_id,
        )
    assert store.list_experiment_trials(experiment_id) == []
    store.close()


@pytest.mark.asyncio
async def test_surviving_known_bad_mutation_blocks_candidate(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[
            VariantSpec("parent", "1.0.0", "GOOD"),
            VariantSpec("candidate", "1.0.1-candidate", "GOOD IMPROVED"),
            VariantSpec("mutation-too-weak", "mutant", "GOOD", "too_weak"),
        ],
        trials=1,
        executor=_executor,
        model_ref={"provider": "fixture", "model": "deterministic"},
        skill_name="local-skill",
    )

    assert summary.status == "rejected"
    assert summary.governance["hard_gate_failures"] == ["mutation_survived:too_weak"]
    trial = next(
        item
        for item in store.list_experiment_trials(summary.experiment_id)
        if item["variant"] == "mutation-too-weak"
    )
    assert trial["status"] == "mutation_survived"
    store.close()


@pytest.mark.asyncio
async def test_skip_execution_mutation_is_injected_at_execution_boundary(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))
    calls = 0

    async def passing_executor(*args):
        nonlocal calls
        calls += 1
        return await _executor(*args)

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[
            VariantSpec("candidate", "1.0.1-candidate", "GOOD"),
            VariantSpec("mutation-skip-execution", "mutant", "GOOD", "skip_execution"),
        ],
        trials=1,
        executor=passing_executor,
        model_ref={"provider": "fixture", "model": "deterministic"},
        skill_name="local-skill",
    )

    assert summary.status == "validated"
    assert summary.variant_counts["mutation-skip-execution"]["fail"] == 1
    assert calls == 1
    mutation_execution = next(
        item
        for item in store.get_skill_executions("local-skill", limit=10)
        if item["variant"] == "mutation-skip-execution"
    )
    assert mutation_execution["tool_trace"][0]["operator"] == "skip_execution"
    store.close()


@pytest.mark.asyncio
async def test_runner_stops_when_candidate_failure_makes_promotion_impossible(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    runner = EvaluationExperimentRunner(store, base_dir=tmp_path)
    corpus = load_evaluation_corpus(_corpus(tmp_path))

    async def failing(*args):
        return BenchmarkExecution(output="failed", final_state={"done": False})

    summary = await runner.run(
        corpus=corpus,
        split="development",
        variants=[
            VariantSpec("parent", "1.0.0", "GOOD"),
            VariantSpec("candidate", "1.0.1-candidate", "BROKEN"),
        ],
        trials=3,
        executor=failing,
        model_ref={"provider": "fixture", "model": "deterministic"},
        skill_name="local-skill",
    )

    assert summary.status == "rejected"
    assert summary.planned_trials == 6
    assert summary.valid_trials == 1
    assert summary.early_stop["reason"] == "candidate_failure"
    assert len(store.list_experiment_trials(summary.experiment_id)) == 1
    assert store.list_experiment_trials(summary.experiment_id)[0]["variant"] == "candidate"
    store.close()


def test_provider_rate_limit_is_not_classified_as_code_regression():
    rate_limit = type("RateLimitError", (Exception,), {})
    assert (
        EvaluationExperimentRunner._classify_exception(rate_limit("quota"))
        == "external_dependency_unavailable"
    )
    assert (
        EvaluationExperimentRunner._classify_exception(
            RuntimeError("Codex endpoint returned 429: quota")
        )
        == "external_dependency_unavailable"
    )
    assert (
        EvaluationExperimentRunner._classify_exception(
            RuntimeError("Codex endpoint returned 400: malformed request")
        )
        == "code_regression"
    )
    connect_error = type("ConnectError", (Exception,), {})
    assert (
        EvaluationExperimentRunner._classify_exception(connect_error())
        == "external_dependency_unavailable"
    )
