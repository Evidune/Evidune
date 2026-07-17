"""Execution-grounded benchmark experiment orchestration and artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.benchmark import (
    BenchmarkAdapterRegistry,
    BenchmarkExecutor,
    EvaluationCorpus,
    corpus_source_root,
    validate_evaluation_corpus,
    write_json,
)
from adapters.fixture_benchmark import FixtureBenchmarkAdapter
from core.evaluation_models import ExperimentRunSummary, VariantSpec
from core.evaluation_policy import EvaluationPolicyMixin
from core.evaluation_reports import EvaluationReportMixin
from core.evaluation_trials import EvaluationTrialMixin
from memory.store import MemoryStore
from skills.governance import EvaluationVerdict


class EvaluationExperimentRunner(
    EvaluationTrialMixin,
    EvaluationPolicyMixin,
    EvaluationReportMixin,
):
    def __init__(
        self,
        memory: MemoryStore,
        *,
        base_dir: Path,
        registry: BenchmarkAdapterRegistry | None = None,
    ) -> None:
        self.memory = memory
        self.base_dir = base_dir.resolve()
        self.registry = registry or BenchmarkAdapterRegistry()
        if "fixture" not in self.registry.names():
            self.registry.register(FixtureBenchmarkAdapter())

    def _artifact_dir(self, experiment_id: str) -> Path:
        return self.base_dir / ".evidune" / "runtime" / "evaluations" / experiment_id

    @staticmethod
    def _planned_calls(task_count: int, variants: list[VariantSpec], trials: int) -> int:
        return task_count * len(variants) * trials

    @staticmethod
    def _check_budget(corpus: EvaluationCorpus, planned_model_calls: int) -> None:
        maximum = int(corpus.budget.get("max_model_calls") or 0)
        if maximum and planned_model_calls > maximum:
            raise ValueError(
                f"Experiment needs up to {planned_model_calls} model calls, "
                f"exceeding corpus budget {maximum}"
            )

    async def run(
        self,
        *,
        corpus: EvaluationCorpus,
        split: str,
        variants: list[VariantSpec],
        trials: int,
        executor: BenchmarkExecutor,
        model_ref: dict[str, Any],
        skill_name: str,
        source_execution_ids: list[int] | None = None,
        experiment_id: str = "",
    ) -> ExperimentRunSummary:
        if not variants:
            raise ValueError("At least one Skill variant is required")
        if trials < 1:
            raise ValueError("trials must be positive")
        validation = validate_evaluation_corpus(
            corpus, source_root=corpus_source_root(corpus, self.base_dir)
        )
        if not validation.ok:
            raise ValueError("Invalid evaluation corpus: " + "; ".join(validation.errors))
        adapter = self.registry.get(corpus.adapter)
        if adapter.revision != corpus.adapter_revision:
            raise ValueError(
                f"Adapter revision mismatch: manifest={corpus.adapter_revision}, "
                f"runtime={adapter.revision}"
            )
        parent = next((item for item in variants if item.name == "parent"), variants[0])
        candidate_variant = next((item for item in variants if item.name == "candidate"), None)
        candidate = candidate_variant or parent
        tasks = corpus.task_refs(split, skill_name=skill_name)
        planned = self._planned_calls(len(tasks), variants, trials)
        turns_per_trial = int(corpus.budget.get("max_model_turns_per_trial") or 1)
        planned_model_calls = planned * turns_per_trial
        self._check_budget(corpus, planned_model_calls)
        run_budget = {
            **corpus.budget,
            "planned_trials": planned,
            "planned_model_calls": planned_model_calls,
            "trials_per_variant": trials,
        }
        experiment_id = self._bind_or_create_experiment(
            experiment_id=experiment_id,
            corpus=corpus,
            split=split,
            model_ref=model_ref,
            skill_name=skill_name,
            parent=parent,
            candidate=candidate,
            source_execution_ids=source_execution_ids or [],
            run_budget=run_budget,
        )
        self._assert_holdout_source_disjoint(experiment_id, tasks, split)
        artifact_dir = self._artifact_dir(experiment_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(
            artifact_dir=artifact_dir,
            experiment_id=experiment_id,
            corpus=corpus,
            adapter=adapter,
            split=split,
            tasks=tasks,
            variants=variants,
            trials=trials,
            model_ref=model_ref,
            planned_model_calls=planned_model_calls,
        )
        contract_digest = self.memory.record_contract_snapshot(
            contract_kind="benchmark",
            contract={
                "corpus_id": corpus.corpus_id,
                "manifest_digest": corpus.manifest_digest,
                "adapter": adapter.adapter_id,
                "adapter_revision": adapter.revision,
                "evaluator": corpus.evaluator,
            },
            contract_version=corpus.adapter_revision,
            source=corpus.manifest_path,
        )
        configured_minimum = int(corpus.evaluator.get("minimum_valid_trials") or 0)
        required_trials = configured_minimum or trials
        if required_trials > trials:
            raise ValueError(
                f"Experiment requests {trials} trials but requires "
                f"{required_trials} valid trials"
            )
        valid, invalid, counts, results, valid_tasks, early_stop = await self._run_trials(
            corpus=corpus,
            split=split,
            tasks=tasks,
            variants=variants,
            trials=trials,
            adapter=adapter,
            executor=executor,
            model_ref=model_ref,
            skill_name=skill_name,
            experiment_id=experiment_id,
            artifact_dir=artifact_dir,
            contract_digest=contract_digest,
            candidate_name=candidate.name,
            fail_fast_candidate_name=(
                candidate_variant.name
                if candidate_variant and bool(corpus.evaluator.get(f"fail_fast_{split}", True))
                else ""
            ),
            required_candidate_trials=required_trials,
        )
        governance = self._experiment_governance(
            corpus,
            results,
            valid_tasks,
            variants=variants,
            variant_counts=counts,
            split=split,
            required_trials=required_trials,
            skill_name=skill_name,
            early_stop=early_stop,
        )
        if governance.promotable:
            status = "validated"
        elif governance.verdict == EvaluationVerdict.INCONCLUSIVE:
            status = "inconclusive"
        else:
            status = "rejected"
        self.memory.transition_skill_experiment(
            experiment_id,
            status,
            reason=governance.reason,
            evidence={
                "governance": governance.to_dict(),
                "artifact_dir": str(artifact_dir),
                "valid_trials": valid,
                "invalid_trials": invalid,
                "variant_counts": counts,
                "early_stop": early_stop,
            },
        )
        summary = ExperimentRunSummary(
            experiment_id=experiment_id,
            corpus_id=corpus.corpus_id,
            split=split,
            status=status,
            artifact_dir=str(artifact_dir),
            planned_trials=planned,
            valid_trials=valid,
            invalid_trials=invalid,
            variant_counts=counts,
            governance=governance.to_dict(),
            early_stop=early_stop,
        )
        write_json(artifact_dir / "summary.json", summary.to_dict())
        (artifact_dir / "summary.md").write_text(self._summary_markdown(summary), encoding="utf-8")
        (artifact_dir / "junit.xml").write_text(self._junit(experiment_id), encoding="utf-8")
        return summary

    def _bind_or_create_experiment(
        self,
        *,
        experiment_id: str,
        corpus: EvaluationCorpus,
        split: str,
        model_ref: dict[str, Any],
        skill_name: str,
        parent: VariantSpec,
        candidate: VariantSpec,
        source_execution_ids: list[int],
        run_budget: dict[str, Any],
    ) -> str:
        if experiment_id:
            existing = self.memory.get_skill_experiment(experiment_id)
            if existing is None:
                raise ValueError(f"Unknown Skill experiment: {experiment_id}")
            expected = {
                "skill_name": skill_name,
                "parent_digest": parent.digest,
                "candidate_digest": candidate.digest,
            }
            mismatches = [key for key, value in expected.items() if existing[key] != value]
            if mismatches:
                raise ValueError(
                    "Staged candidate does not match evaluation inputs: " + ", ".join(mismatches)
                )
            if existing["status"] in {"inconclusive", "rejected"}:
                return self.memory.create_skill_experiment(
                    skill_name=existing["skill_name"],
                    parent_version=existing["parent_version"],
                    parent_digest=existing["parent_digest"],
                    parent_content=existing["parent_content"],
                    candidate_version=existing["candidate_version"],
                    candidate_digest=existing["candidate_digest"],
                    candidate_content=existing["candidate_content"],
                    source_execution_ids=existing["source_execution_ids"],
                    corpus_id=corpus.corpus_id,
                    split=split,
                    model_ref=model_ref,
                    budget=run_budget,
                    policy={**corpus.evaluator, "retry_of": experiment_id},
                )
            self.memory.bind_skill_experiment_validation(
                experiment_id,
                corpus_id=corpus.corpus_id,
                split=split,
                model_ref=model_ref,
                budget=run_budget,
                policy=corpus.evaluator,
            )
            return experiment_id
        return self.memory.create_skill_experiment(
            skill_name=skill_name,
            parent_version=parent.version,
            parent_digest=parent.digest,
            parent_content=parent.content,
            candidate_version=candidate.version,
            candidate_digest=candidate.digest,
            candidate_content=candidate.content,
            source_execution_ids=source_execution_ids,
            corpus_id=corpus.corpus_id,
            split=split,
            model_ref=model_ref,
            budget=run_budget,
            policy=corpus.evaluator,
        )
