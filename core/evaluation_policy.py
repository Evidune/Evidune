"""Replay and deterministic governance policy for Skill experiments."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from adapters.benchmark import EvaluationCorpus, write_json
from agent.benchmark_executor import BenchmarkBudgetExceeded, InvalidBenchmarkResponse
from core.evaluation_models import VariantSpec
from skills.governance import (
    EvaluationResult,
    EvaluationVerdict,
    GovernanceDecision,
    GovernancePolicy,
    decide_governance,
)


class EvaluationPolicyMixin:
    def _assert_holdout_source_disjoint(
        self, experiment_id: str, tasks: list[Any], split: str
    ) -> None:
        """Reject holdout tasks previously used to produce the candidate."""
        if split != "holdout":
            return
        experiment = self.memory.get_skill_experiment(experiment_id)
        if experiment is None:
            raise ValueError(f"Unknown Skill experiment: {experiment_id}")
        holdout_ids = {str(task.id) for task in tasks}
        seen: set[str] = set()
        for execution_id in experiment.get("source_execution_ids") or []:
            execution = self.memory.get_skill_executions_by_id(int(execution_id))
            task_id = str((execution or {}).get("benchmark_task_id") or "")
            if task_id in holdout_ids:
                seen.add(task_id)
        if seen:
            raise ValueError(
                "Candidate source executions overlap holdout tasks: " + ", ".join(sorted(seen))
            )

    def replay(self, experiment_id: str) -> GovernanceDecision:
        experiment = self.memory.get_skill_experiment(experiment_id)
        if experiment is None:
            raise ValueError(f"Unknown Skill experiment: {experiment_id}")
        candidate_version = experiment["candidate_version"]
        trials = self.memory.list_experiment_trials(experiment_id)
        valid_execution_ids = {
            int(trial["execution_id"])
            for trial in trials
            if trial.get("execution_id")
            and trial["status"] in {"passed", "failed"}
            and (
                (execution := self.memory.get_skill_executions_by_id(int(trial["execution_id"])))
                is not None
            )
            and execution["skill_version"] == candidate_version
        }
        results = [
            EvaluationResult.from_dict(self._stored_result_payload(item))
            for item in self.memory.list_evaluation_results(
                skill_name=experiment["skill_name"],
                skill_version=candidate_version,
                limit=10000,
            )
            if int(item["execution_id"] or 0) in valid_execution_ids
        ]
        policy = GovernancePolicy(
            required_evaluators=list(experiment["policy"].get("required_evaluators") or []),
            minimum_attribution=str(experiment["policy"].get("minimum_attribution") or "unknown"),
        )
        decision = decide_governance(results, policy)
        write_json(
            self._artifact_dir(experiment_id) / "replay.json",
            {"experiment_id": experiment_id, "governance": decision.to_dict()},
        )
        return decision

    @staticmethod
    def _stored_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "execution_id",
            "skill_name",
            "skill_version",
            "evaluator_id",
            "evaluator_revision",
            "evaluator_type",
            "contract_digest",
            "verdict",
            "score",
            "uncertainty",
            "dimensions",
            "failure_modes",
            "evidence_refs",
            "hard_gate_failures",
            "attribution_grade",
            "reasoning",
            "metadata",
        }
        return {key: value for key, value in payload.items() if key in allowed}

    def _experiment_governance(
        self,
        corpus: EvaluationCorpus,
        results: list[EvaluationResult],
        valid_tasks_by_variant: dict[str, dict[str, int]],
        variants: list[VariantSpec],
        variant_counts: dict[str, dict[str, int]],
        *,
        split: str,
        required_trials: int,
        skill_name: str,
        early_stop: dict[str, Any] | None = None,
    ) -> GovernanceDecision:
        early_stop = early_stop or {}
        if early_stop.get("reason") == "mutation_survived":
            operator = str(early_stop.get("mutation_operator") or "unknown")
            return GovernanceDecision(
                verdict=EvaluationVerdict.FAIL,
                promotable=False,
                hard_gate_failures=[f"mutation_survived:{operator}"],
                reason="known-bad Skill mutation survived its evaluator",
            )
        if early_stop.get("reason") == "candidate_failure":
            policy = GovernancePolicy(
                required_evaluators=list(corpus.evaluator.get("required_evaluators") or []),
                minimum_attribution=str(corpus.evaluator.get("minimum_attribution") or "unknown"),
            )
            return decide_governance(results, policy)
        candidate = next(
            (variant for variant in variants if variant.name == "candidate"), variants[0]
        )
        candidate_tasks = valid_tasks_by_variant.get(candidate.name, {})
        missing = [
            task.id
            for task in corpus.task_refs(split, skill_name=skill_name)
            if candidate_tasks.get(task.id, 0) < required_trials
        ]
        if missing:
            return GovernanceDecision(
                verdict=EvaluationVerdict.INCONCLUSIVE,
                promotable=False,
                missing_evaluators=[f"valid_trials:{task_id}" for task_id in missing],
                reason="minimum valid paired trials not reached",
            )
        mutations = [variant for variant in variants if variant.mutation_operator]
        required_mutations = set(corpus.evaluator.get("required_mutations") or [])
        present_mutations = {variant.mutation_operator for variant in mutations}
        missing_mutations = sorted(required_mutations - present_mutations)
        if missing_mutations:
            return GovernanceDecision(
                verdict=EvaluationVerdict.INCONCLUSIVE,
                promotable=False,
                missing_evaluators=[f"mutation:{name}" for name in missing_mutations],
                reason="required mutation operators were not executed",
            )
        invalid_mutations = [
            f"{variant.mutation_operator}:{task.id}"
            for variant in mutations
            for task in corpus.task_refs(split, skill_name=skill_name)
            if valid_tasks_by_variant.get(variant.name, {}).get(task.id, 0) < required_trials
        ]
        if invalid_mutations:
            return GovernanceDecision(
                verdict=EvaluationVerdict.INCONCLUSIVE,
                promotable=False,
                missing_evaluators=[f"valid_mutation_trials:{name}" for name in invalid_mutations],
                reason="mutation test did not reach the minimum valid trials per task",
            )
        survived = [
            variant.mutation_operator
            for variant in mutations
            if variant_counts[variant.name]["pass"] > 0
        ]
        if survived:
            return GovernanceDecision(
                verdict=EvaluationVerdict.FAIL,
                promotable=False,
                hard_gate_failures=[f"mutation_survived:{name}" for name in survived],
                reason="known-bad Skill mutation survived its evaluator",
            )
        policy = GovernancePolicy(
            required_evaluators=list(corpus.evaluator.get("required_evaluators") or []),
            minimum_attribution=str(corpus.evaluator.get("minimum_attribution") or "unknown"),
        )
        return decide_governance(results, policy)

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        if isinstance(exc, InvalidBenchmarkResponse):
            return "invalid_model_response"
        if isinstance(exc, BenchmarkBudgetExceeded):
            return "budget_exhausted"
        if exc.__class__.__name__ in {
            "APIConnectionError",
            "AuthenticationError",
            "ConnectError",
            "ConnectTimeout",
            "NetworkError",
            "RateLimitError",
            "ReadTimeout",
            "RemoteProtocolError",
            "_CodexUnauthorized",
        }:
            return "external_dependency_unavailable"
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None:
            match = re.search(r"(?:endpoint|provider) returned (\d{3})", str(exc), re.IGNORECASE)
            status = int(match.group(1)) if match else None
        if isinstance(status, int) and (status in {401, 403, 408, 409, 429} or status >= 500):
            return "external_dependency_unavailable"
        if isinstance(exc, TimeoutError | asyncio.TimeoutError | ConnectionError):
            return "external_dependency_unavailable"
        if isinstance(exc, FileNotFoundError | ImportError):
            return "environment_unavailable"
        return "code_regression"
