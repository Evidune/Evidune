"""Deterministic local benchmark adapter used for contracts and live-model smoke runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.benchmark import (
    BenchmarkAdapter,
    BenchmarkExecution,
    BenchmarkExecutor,
    BenchmarkObservation,
    CorpusTask,
    EvaluationCorpus,
    PreparedTask,
    ResetResult,
    write_json,
)
from skills.governance import EvaluationResult, canonical_digest


def _contains(actual: Any, expected: Any) -> bool:
    """Return whether actual recursively contains expected."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_contains(item, expected_item) for item in actual) for expected_item in expected
        )
    return actual == expected


def _forbidden_hits(actual: Any, forbidden: dict[str, Any]) -> list[str]:
    if not isinstance(actual, dict):
        return []
    return [
        key for key, value in forbidden.items() if key in actual and _contains(actual[key], value)
    ]


class FixtureBenchmarkAdapter(BenchmarkAdapter):
    adapter_id = "fixture"
    revision = "v1"

    def prepare(
        self,
        corpus: EvaluationCorpus,
        task: CorpusTask,
        split: str,
        workspace: Path,
    ) -> PreparedTask:
        workspace.mkdir(parents=True, exist_ok=True)
        write_json(workspace / "initial_state.json", task.initial_state)
        return PreparedTask(
            corpus_id=corpus.corpus_id,
            task=task,
            split=split,
            workspace=str(workspace),
            agent_context={
                "initial_state_path": str(workspace / "initial_state.json"),
                "metadata": {
                    key: value
                    for key, value in task.metadata.items()
                    if key not in {"required_output_contains", "forbidden_output_contains"}
                },
            },
        )

    async def execute(
        self,
        prepared: PreparedTask,
        skill_content: str,
        model_ref: dict[str, Any],
        trial: int,
        executor: BenchmarkExecutor,
    ) -> BenchmarkExecution:
        return await executor(prepared, skill_content, model_ref, trial)

    def collect(self, execution: BenchmarkExecution) -> list[BenchmarkObservation]:
        return [
            BenchmarkObservation("output", {"text": execution.output}),
            BenchmarkObservation("state", execution.final_state),
            BenchmarkObservation("trace", {"tool_trace": execution.tool_trace}),
        ]

    def evaluate(
        self,
        prepared: PreparedTask,
        execution: BenchmarkExecution,
        execution_id: int,
        evaluator_revision: str,
    ) -> list[EvaluationResult]:
        task = prepared.task
        expected_ok = _contains(execution.final_state, task.expected_state)
        forbidden_hits = _forbidden_hits(execution.final_state, task.forbidden_state)
        required_text = [str(value) for value in task.metadata.get("required_output_contains", [])]
        forbidden_text = [
            str(value) for value in task.metadata.get("forbidden_output_contains", [])
        ]
        missing_text = [value for value in required_text if value not in execution.output]
        forbidden_output_hits = [value for value in forbidden_text if value in execution.output]
        hard_failures = [f"forbidden_state:{value}" for value in forbidden_hits]
        hard_failures.extend(f"forbidden_output:{value}" for value in forbidden_output_hits)
        failures: list[str] = []
        if not expected_ok:
            failures.append("expected_state_not_reached")
        if missing_text:
            failures.append("required_output_missing")
        if hard_failures:
            failures.append("forbidden_side_effect")
        verdict = "pass" if not failures else "fail"
        return [
            EvaluationResult(
                execution_id=execution_id,
                evaluator_id="fixture_state_and_output",
                evaluator_revision=evaluator_revision,
                evaluator_type="state_diff",
                verdict=verdict,
                uncertainty="low",
                dimensions={
                    "task_completed": expected_ok and not missing_text,
                    "expected_state_reached": expected_ok,
                    "missing_required_output": missing_text,
                    "forbidden_state_hits": forbidden_hits,
                    "forbidden_output_hits": forbidden_output_hits,
                },
                failure_modes=failures,
                hard_gate_failures=hard_failures,
                evidence_refs=[f"benchmark://{prepared.corpus_id}/{task.id}"],
                attribution_grade="direct",
                metadata={"task_id": task.id, "split": prepared.split},
            )
        ]

    def reset(self, prepared: PreparedTask) -> ResetResult:
        path = Path(prepared.workspace) / "initial_state.json"
        if not path.is_file():
            return ResetResult(False, reason="initial state artifact is missing")
        return ResetResult(True, state_digest=canonical_digest(prepared.task.initial_state))
