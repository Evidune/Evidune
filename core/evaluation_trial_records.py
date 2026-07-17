"""Persistence helpers for benchmark trial evidence and terminal records."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from adapters.benchmark import write_json
from core.evaluation_models import VariantSpec, utc_now
from skills.governance import EvaluationResult, text_digest


class EvaluationTrialRecordMixin:
    def _load_existing_trials(
        self,
        experiment_id: str,
        variants: list[VariantSpec],
        candidate_name: str,
    ) -> tuple[
        dict[tuple[str, str, int], dict[str, Any]],
        int,
        int,
        dict[str, dict[str, int]],
        list[EvaluationResult],
        dict[str, dict[str, int]],
    ]:
        counts = {
            variant.name: {"valid": 0, "invalid": 0, "pass": 0, "fail": 0} for variant in variants
        }
        valid_tasks = {variant.name: {} for variant in variants}
        candidate_results: list[EvaluationResult] = []
        valid_trials = invalid_trials = 0
        existing = {
            (row["task_ref"], row["variant"], row["trial_number"]): row
            for row in self.memory.list_experiment_trials(experiment_id)
        }
        for row in existing.values():
            variant_counts = counts.get(row["variant"])
            if variant_counts is None:
                continue
            if row["status"] == "invalid":
                invalid_trials += 1
                variant_counts["invalid"] += 1
                continue
            valid_trials += 1
            variant_counts["valid"] += 1
            variant_counts[
                "pass" if row["status"] in {"passed", "mutation_killed"} else "fail"
            ] += 1
            task_counts = valid_tasks[row["variant"]]
            task_counts[row["task_ref"]] = task_counts.get(row["task_ref"], 0) + 1
            if row["variant"] == candidate_name and row.get("execution_id"):
                for stored in self.memory.list_evaluation_results(execution_id=row["execution_id"]):
                    candidate_results.append(
                        EvaluationResult.from_dict(
                            {
                                name: stored[name]
                                for name in EvaluationResult.__dataclass_fields__
                                if name in stored
                            }
                        )
                    )
        return (
            existing,
            valid_trials,
            invalid_trials,
            counts,
            candidate_results,
            valid_tasks,
        )

    def _bind_immediate_evidence(
        self,
        *,
        execution_id: int,
        skill_name: str,
        variant: VariantSpec,
        task_id: str,
        trial_number: int,
        adapter: Any,
        contract_digest: str,
        started_at: str,
        completed_at: str,
        observations: list[Any],
        artifact: Path,
    ) -> str:
        binding_id = self.memory.create_evidence_binding(
            execution_id=execution_id,
            skill_name=skill_name,
            skill_version=variant.version,
            entity_type="benchmark_task",
            entity_id=task_id,
            intervention={"variant": variant.name, "trial": trial_number},
            observation_plan={"horizons": [{"id": "immediate"}]},
            attribution_policy="direct",
            minimum_evidence_grade="direct",
            probe_digest=text_digest(f"{adapter.adapter_id}:{adapter.revision}"),
            evaluator_digest=contract_digest,
            contract_digest=contract_digest,
        )
        self.memory.transition_evidence_binding(binding_id, "scheduled")
        self.memory.transition_evidence_binding(binding_id, "observing")
        payload = {"observations": [asdict(item) for item in observations]}
        self.memory.record_probe_attempt(
            binding_id=binding_id,
            horizon_id="immediate",
            probe_revision=adapter.revision,
            status="completed",
            payload=payload,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.memory.record_evidence_observation(
            binding_id=binding_id,
            horizon_id="immediate",
            probe_revision=adapter.revision,
            observation_kind="benchmark_bundle",
            payload=payload,
            evidence_ref=f"artifact://{artifact}",
            observed_at=completed_at,
        )
        return binding_id

    def _record_invalid_trial(
        self,
        *,
        experiment_id: str,
        task_id: str,
        split: str,
        variant: str,
        trial_number: int,
        started_at: str,
        trial_dir: Path,
        exc: Exception,
        cleanup: Any = None,
    ) -> None:
        completed_at = utc_now()
        diagnostic = {
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "classification": self._classify_exception(exc),
        }
        exception_diagnostics = getattr(exc, "diagnostics", None)
        if isinstance(exception_diagnostics, dict) and exception_diagnostics:
            diagnostic["diagnostics"] = exception_diagnostics
        if cleanup is not None:
            diagnostic["cleanup"] = asdict(cleanup)
        write_json(trial_dir / "invalid.json", diagnostic)
        self.memory.record_experiment_trial(
            experiment_id=experiment_id,
            task_ref=task_id,
            split=split,
            variant=variant,
            trial_number=trial_number,
            status="invalid",
            classification=diagnostic["classification"],
            result=diagnostic,
            started_at=started_at,
            completed_at=completed_at,
        )

    @staticmethod
    def _trial_status(reset_ok: bool, mutation: str, promotable: bool) -> tuple[str, str]:
        if not reset_ok:
            return "invalid", "environment_unavailable"
        if mutation:
            return ("mutation_survived" if promotable else "mutation_killed"), "mutation_test"
        return ("passed" if promotable else "failed"), "skill_result"
