"""AppWorld benchmark adapter with hidden, state-based evaluation.

The optional AppWorld dependency is imported only when a trial is reset.  This
keeps the core package usable on Python versions and installations that do not
have the benchmark data available.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
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
)
from skills.governance import EvaluationResult, canonical_digest


class AppWorldBenchmarkAdapter(BenchmarkAdapter):
    """Run AppWorld tasks without exposing evaluator ground truth to the LLM."""

    adapter_id = "appworld"
    revision = "v1"

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def prepare(
        self,
        corpus: EvaluationCorpus,
        task: CorpusTask,
        split: str,
        workspace: Path,
    ) -> PreparedTask:
        workspace.mkdir(parents=True, exist_ok=True)
        base_experiment_name = str(
            corpus.environment.get("experiment_name") or "evidune-evaluation"
        )
        trial_suffix = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:12]
        return PreparedTask(
            corpus_id=corpus.corpus_id,
            task=task,
            split=split,
            workspace=str(workspace),
            agent_context={
                "appworld_task_id": str(task.metadata.get("appworld_task_id") or task.id),
                "experiment_name": f"{base_experiment_name}-{trial_suffix}",
                "appworld_root": str(corpus.environment.get("root") or ""),
                "appworld_options": dict(corpus.environment.get("appworld_options") or {}),
                "max_model_turns": int(corpus.budget.get("max_model_turns_per_trial") or 12),
                "max_tool_calls": int(corpus.budget.get("max_tool_calls_per_trial") or 30),
                "trial_timeout_seconds": float(corpus.budget.get("max_trial_seconds") or 0),
                "model_call_timeout_seconds": float(
                    corpus.budget.get("model_call_timeout_seconds") or 120
                ),
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
        session = self._sessions.get(prepared.workspace)
        if not session or session.get("closed"):
            raise RuntimeError("AppWorld trial has not been initialized")
        world = session["world"]
        result = await executor(prepared, skill_content, model_ref, trial)
        evaluation = world.evaluate().to_dict()
        task_completed = bool(world.task_completed())
        session["executed"] = True
        output_directory = getattr(world, "output_directory", "")
        artifact_refs = list(result.artifact_refs)
        if output_directory:
            artifact_refs.append(str(output_directory))
        return replace(
            result,
            final_state={
                **result.final_state,
                "task_completed": task_completed,
                "appworld_success": bool(evaluation.get("success")),
            },
            artifact_refs=artifact_refs,
            metadata={
                **result.metadata,
                "appworld_evaluation": evaluation,
                "appworld_task_id": prepared.agent_context["appworld_task_id"],
            },
        )

    def collect(self, execution: BenchmarkExecution) -> list[BenchmarkObservation]:
        evaluation = dict(execution.metadata.get("appworld_evaluation") or {})
        return [
            BenchmarkObservation("appworld_evaluation", evaluation),
            BenchmarkObservation("appworld_final_state", execution.final_state),
            BenchmarkObservation("trace", {"tool_trace": execution.tool_trace}),
        ]

    def evaluate(
        self,
        prepared: PreparedTask,
        execution: BenchmarkExecution,
        execution_id: int,
        evaluator_revision: str,
    ) -> list[EvaluationResult]:
        evaluation = dict(execution.metadata.get("appworld_evaluation") or {})
        passes = list(evaluation.get("passes") or [])
        failures = list(evaluation.get("failures") or evaluation.get("fails") or [])
        task_completed = bool(execution.final_state.get("task_completed"))
        success = bool(evaluation.get("success")) and task_completed
        failure_labels = sorted(
            {
                str(item.get("label") or "requirement_failed")
                for item in failures
                if isinstance(item, dict)
            }
        )
        task_id = str(prepared.agent_context["appworld_task_id"])
        return [
            EvaluationResult(
                execution_id=execution_id,
                evaluator_id="appworld_state_evaluator",
                evaluator_revision=evaluator_revision,
                evaluator_type="state_diff",
                verdict="pass" if success else "fail",
                uncertainty="low",
                dimensions={
                    "task_completed": task_completed,
                    "requirements_passed": len(passes),
                    "requirements_failed": len(failures),
                    "difficulty": evaluation.get("difficulty"),
                },
                failure_modes=failure_labels or ([] if success else ["appworld_task_failed"]),
                evidence_refs=[f"appworld://{task_id}"],
                attribution_grade="direct",
                reasoning="AppWorld database-state evaluator result",
                metadata={
                    "task_id": task_id,
                    "split": prepared.split,
                    "evaluation": evaluation,
                },
            )
        ]

    def reset(self, prepared: PreparedTask) -> ResetResult:
        """Open before execution and close after execution or an exception."""
        session = self._sessions.get(prepared.workspace)
        if session is None:
            try:
                from appworld import AppWorld

                root = str(prepared.agent_context.get("appworld_root") or "")
                if root:
                    from appworld.common.path_store import path_store

                    path_store.update_root(str(Path(root).expanduser().resolve()))
                options = dict(prepared.agent_context.get("appworld_options") or {})
                world = AppWorld(
                    task_id=prepared.agent_context["appworld_task_id"],
                    experiment_name=prepared.agent_context["experiment_name"],
                    **options,
                )
            except (ImportError, FileNotFoundError, RuntimeError) as exc:
                return ResetResult(False, reason=f"AppWorld unavailable: {exc}")
            prepared.agent_context["appworld_world"] = world
            prepared.agent_context["instruction"] = str(world.task.instruction)
            prepared.agent_context["app_descriptions"] = dict(world.task.app_descriptions)
            self._sessions[prepared.workspace] = {
                "world": world,
                "executed": False,
                "closed": False,
            }
            return ResetResult(
                True,
                state_digest=canonical_digest(
                    {
                        "task_id": prepared.agent_context["appworld_task_id"],
                        "split": prepared.split,
                    }
                ),
            )

        if not session.get("closed"):
            try:
                session["world"].close()
            except Exception as exc:  # cleanup failure makes the trial invalid
                return ResetResult(False, reason=f"AppWorld cleanup failed: {exc}")
            finally:
                session["closed"] = True
                prepared.agent_context.pop("appworld_world", None)
        return ResetResult(True, state_digest="closed")
