"""Isolated execution of benchmark trials and evidence persistence."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from adapters.benchmark import BenchmarkExecutor, EvaluationCorpus, write_json
from core.evaluation_environment import safe_reset
from core.evaluation_faults import execute_variant
from core.evaluation_models import VariantSpec, utc_now
from core.evaluation_trial_records import EvaluationTrialRecordMixin
from skills.governance import EvaluationResult, decide_governance


class EvaluationTrialMixin(EvaluationTrialRecordMixin):
    async def _run_trials(
        self,
        *,
        corpus: EvaluationCorpus,
        split: str,
        tasks: list[Any],
        variants: list[VariantSpec],
        trials: int,
        adapter: Any,
        executor: BenchmarkExecutor,
        model_ref: dict[str, Any],
        skill_name: str,
        experiment_id: str,
        artifact_dir: Path,
        contract_digest: str,
        candidate_name: str,
        fail_fast_candidate_name: str = "",
        required_candidate_trials: int = 0,
    ) -> tuple[
        int,
        int,
        dict[str, dict[str, int]],
        list[EvaluationResult],
        dict[str, dict[str, int]],
        dict[str, Any],
    ]:
        valid_trials = 0
        invalid_trials = 0
        counts = {
            variant.name: {"valid": 0, "invalid": 0, "pass": 0, "fail": 0} for variant in variants
        }
        candidate_results: list[EvaluationResult] = []
        valid_tasks_by_variant: dict[str, dict[str, int]] = {
            variant.name: {} for variant in variants
        }
        early_stop: dict[str, Any] = {}
        execution_variants = list(variants)
        if fail_fast_candidate_name:
            execution_variants.sort(
                key=lambda variant: (
                    0
                    if variant.name == fail_fast_candidate_name
                    else (1 if variant.mutation_operator else 2)
                )
            )

        def candidate_validity_impossible(
            task_id: str, variant_name: str, trial_number: int
        ) -> dict[str, Any]:
            if variant_name != fail_fast_candidate_name or required_candidate_trials <= 0:
                return {}
            valid_for_task = valid_tasks_by_variant.get(variant_name, {}).get(task_id, 0)
            remaining = trials - trial_number
            if valid_for_task + remaining >= required_candidate_trials:
                return {}
            return {
                "reason": "candidate_insufficient_valid_trials",
                "task_id": task_id,
                "variant": variant_name,
                "trial": trial_number,
                "required_valid_trials": required_candidate_trials,
                "valid_trials": valid_for_task,
                "remaining_trials": remaining,
            }

        def finish(stop: dict[str, Any]):
            return (
                valid_trials,
                invalid_trials,
                counts,
                candidate_results,
                valid_tasks_by_variant,
                stop,
            )

        for task in tasks:
            for variant in execution_variants:
                for trial_number in range(1, trials + 1):
                    if (
                        required_candidate_trials > 0
                        and valid_tasks_by_variant[variant.name].get(task.id, 0)
                        >= required_candidate_trials
                    ):
                        break
                    trial_dir = artifact_dir / "trials" / task.id / variant.name / str(trial_number)
                    started_at = utc_now()
                    try:
                        prepared = adapter.prepare(corpus, task, split, trial_dir / "workspace")
                        reset_before = safe_reset(adapter, prepared)
                    except Exception as exc:
                        invalid_trials += 1
                        counts[variant.name]["invalid"] += 1
                        self._record_invalid_trial(
                            experiment_id=experiment_id,
                            task_id=task.id,
                            split=split,
                            variant=variant.name,
                            trial_number=trial_number,
                            started_at=started_at,
                            trial_dir=trial_dir,
                            exc=exc,
                        )
                        early_stop = candidate_validity_impossible(
                            task.id, variant.name, trial_number
                        )
                        if early_stop:
                            return finish(early_stop)
                        continue
                    if not reset_before.ok:
                        invalid_trials += 1
                        counts[variant.name]["invalid"] += 1
                        self.memory.record_experiment_trial(
                            experiment_id=experiment_id,
                            task_ref=task.id,
                            split=split,
                            variant=variant.name,
                            trial_number=trial_number,
                            status="invalid",
                            classification="environment_unavailable",
                            result={"reason": reset_before.reason},
                            started_at=started_at,
                            completed_at=utc_now(),
                        )
                        early_stop = candidate_validity_impossible(
                            task.id, variant.name, trial_number
                        )
                        if early_stop:
                            return finish(early_stop)
                        continue
                    try:
                        execution = await execute_variant(
                            adapter=adapter,
                            prepared=prepared,
                            variant=variant,
                            model_ref=model_ref,
                            trial=trial_number,
                            executor=executor,
                        )
                        completed_at = utc_now()
                        execution_id = self.memory.record_execution(
                            skill_name=skill_name,
                            skill_version=variant.version,
                            skill_digest=variant.digest,
                            experiment_id=experiment_id,
                            corpus_id=corpus.corpus_id,
                            benchmark_task_id=task.id,
                            variant=variant.name,
                            user_input=str(
                                prepared.agent_context.get("instruction") or task.instruction
                            ),
                            assistant_output=execution.output,
                            tool_trace=execution.tool_trace,
                            artifact_refs=execution.artifact_refs,
                            model_ref=model_ref,
                            execution_contract_digest=contract_digest,
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                        observations = adapter.collect(execution)
                        binding_id = self._bind_immediate_evidence(
                            execution_id=execution_id,
                            skill_name=skill_name,
                            variant=variant,
                            task_id=task.id,
                            trial_number=trial_number,
                            adapter=adapter,
                            contract_digest=contract_digest,
                            started_at=started_at,
                            completed_at=completed_at,
                            observations=observations,
                            artifact=trial_dir / "execution.json",
                        )
                        results = [
                            replace(
                                result,
                                skill_name=skill_name,
                                skill_version=variant.version,
                                contract_digest=contract_digest,
                                metadata={
                                    **result.metadata,
                                    "variant": variant.name,
                                    "trial": trial_number,
                                    "mutation_operator": variant.mutation_operator,
                                },
                            )
                            for result in adapter.evaluate(
                                prepared, execution, execution_id, adapter.revision
                            )
                        ]
                        for result in results:
                            self.memory.record_evaluation_result(result.to_dict())
                        self.memory.transition_evidence_binding(binding_id, "evaluated")
                        reset_after = safe_reset(adapter, prepared)
                        trial_verdict = decide_governance(results)
                        status, classification = self._trial_status(
                            reset_after.ok, variant.mutation_operator, trial_verdict.promotable
                        )
                        self.memory.record_experiment_trial(
                            experiment_id=experiment_id,
                            task_ref=task.id,
                            split=split,
                            variant=variant.name,
                            trial_number=trial_number,
                            status=status,
                            classification=classification,
                            execution_id=execution_id,
                            result={
                                "governance": trial_verdict.to_dict(),
                                "reset": asdict(reset_after),
                            },
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                        write_json(
                            trial_dir / "execution.json",
                            {
                                "execution_id": execution_id,
                                "output": execution.output,
                                "final_state": execution.final_state,
                                "tool_trace": execution.tool_trace,
                                "observations": [asdict(item) for item in observations],
                                "evaluations": [item.to_dict() for item in results],
                                "governance": trial_verdict.to_dict(),
                                "reset": asdict(reset_after),
                            },
                        )
                        if reset_after.ok:
                            valid_trials += 1
                            counts[variant.name]["valid"] += 1
                            counts[variant.name][
                                "pass" if trial_verdict.promotable else "fail"
                            ] += 1
                            valid_tasks_by_variant[variant.name][task.id] = (
                                valid_tasks_by_variant[variant.name].get(task.id, 0) + 1
                            )
                            if variant.name == candidate_name:
                                candidate_results.extend(results)
                            if (
                                variant.name == fail_fast_candidate_name
                                and trial_verdict.verdict.value == "fail"
                            ):
                                early_stop = {
                                    "reason": "candidate_failure",
                                    "task_id": task.id,
                                    "variant": variant.name,
                                    "trial": trial_number,
                                }
                                return finish(early_stop)
                            if variant.mutation_operator and trial_verdict.promotable:
                                early_stop = {
                                    "reason": "mutation_survived",
                                    "task_id": task.id,
                                    "variant": variant.name,
                                    "trial": trial_number,
                                    "mutation_operator": variant.mutation_operator,
                                }
                                return finish(early_stop)
                        else:
                            invalid_trials += 1
                            counts[variant.name]["invalid"] += 1
                    except Exception as exc:
                        invalid_trials += 1
                        counts[variant.name]["invalid"] += 1
                        cleanup = safe_reset(adapter, prepared)
                        self._record_invalid_trial(
                            experiment_id=experiment_id,
                            task_id=task.id,
                            split=split,
                            variant=variant.name,
                            trial_number=trial_number,
                            started_at=started_at,
                            trial_dir=trial_dir,
                            exc=exc,
                            cleanup=cleanup,
                        )
                        early_stop = candidate_validity_impossible(
                            task.id, variant.name, trial_number
                        )
                        if early_stop:
                            return finish(early_stop)
        return finish(early_stop)
