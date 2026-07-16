"""CLI handlers for corpus verification and Skill experiments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from adapters.appworld_benchmark import AppWorldBenchmarkAdapter
from adapters.benchmark import (
    BenchmarkAdapterRegistry,
    corpus_source_root,
    load_evaluation_corpus,
    validate_evaluation_corpus,
)
from adapters.fixture_benchmark import FixtureBenchmarkAdapter
from adapters.skill_catalog import catalog_source_root, load_skill_catalog, validate_skill_catalog
from agent.appworld_executor import AppWorldLLMBenchmarkExecutor
from agent.benchmark_executor import LiveLLMBenchmarkExecutor
from agent.iteration_harness import IterationHarness, build_decision_packet
from agent.llm import create_llm_client
from agent.skill_rewriter import propose_skill_rewrite
from core.appworld_corpus import write_appworld_manifest
from core.config import EviduneConfig
from core.corpus_sources import sync_corpus_sources, sync_pinned_sources
from core.evaluation_lifecycle import promote as _promote
from core.evaluation_lifecycle import rollback as _rollback
from core.evaluation_runner import EvaluationExperimentRunner, VariantSpec
from core.runtime_paths import resolve_memory_path
from memory.store import MemoryStore
from skills.loader import parse_skill
from skills.mutations import mutate_skill


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _print_json(payload: dict[str, Any]) -> None:
    import json

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _variants(args, base_dir: Path, memory: MemoryStore) -> tuple[str, list[VariantSpec]]:
    if not args.skill_path:
        raise ValueError("eval run requires --skill-path")
    skill_path = _resolve(base_dir, args.skill_path)
    parent = parse_skill(skill_path)
    parent_content = skill_path.read_text(encoding="utf-8")
    mutation_base_version = parent.version
    mutation_base_content = parent_content
    variants = []
    if args.with_baseline:
        variants.append(VariantSpec("baseline", "no-skill", ""))
    variants.append(VariantSpec("parent", parent.version, parent_content))
    if args.experiment_id:
        experiment = memory.get_skill_experiment(args.experiment_id)
        if experiment is None:
            raise ValueError(f"Unknown Skill experiment: {args.experiment_id}")
        variants.append(
            VariantSpec(
                "candidate",
                experiment["candidate_version"],
                experiment["candidate_content"],
            )
        )
        mutation_base_version = experiment["candidate_version"]
        mutation_base_content = experiment["candidate_content"]
    elif args.candidate_path:
        candidate_path = _resolve(base_dir, args.candidate_path)
        candidate = parse_skill(candidate_path)
        variants.append(
            VariantSpec("candidate", candidate.version, candidate_path.read_text(encoding="utf-8"))
        )
        mutation_base_version = candidate.version
        mutation_base_content = candidate_path.read_text(encoding="utf-8")
    for operator in args.mutation or []:
        mutation = mutate_skill(mutation_base_content, operator)
        if not mutation.changed:
            raise ValueError(f"Mutation {operator} did not apply to {skill_path}")
        variants.append(
            VariantSpec(
                f"mutation-{operator}",
                f"{mutation_base_version}-mutant-{operator}",
                mutation.content,
                operator,
            )
        )
    return args.skill_name or parent.name, variants


async def _stage_candidate_from_evaluation(
    *,
    args,
    base_dir: Path,
    memory: MemoryStore,
    llm,
) -> dict[str, Any]:
    skill_path = _resolve(base_dir, args.skill_path)
    skill = parse_skill(skill_path)
    current = skill_path.read_text(encoding="utf-8")
    packet = build_decision_packet(
        memory,
        skill=skill,
        current=current,
        surface="eval",
        task_kind="benchmark_iteration",
    )
    workflow = IterationHarness(memory)
    if workflow.rewrite_is_due(packet):
        packet.llm_rewrite_proposal = await propose_skill_rewrite(llm, packet)
    decision = workflow.run(packet=packet)
    experiment_id = ""
    if decision.update.path.startswith("candidate://"):
        experiment_id = decision.update.path.removeprefix("candidate://")
    return {
        "decision": decision.decision,
        "skill_status": decision.skill_status,
        "candidate_experiment_id": experiment_id,
        "active_skill_changed": decision.update.has_changes,
        "harness_task_id": decision.task.id,
    }


async def handle_evaluation_command(
    config: EviduneConfig,
    base_dir: Path,
    args,
) -> int:
    action = args.subcommand or "corpus"
    if action == "sources":
        if args.target not in (None, "verify", "sync"):
            raise ValueError("eval sources supports verify and sync")
        if not args.catalog:
            raise ValueError("eval sources requires --catalog")
        catalog = load_skill_catalog(_resolve(base_dir, args.catalog))
        source_root = catalog_source_root(catalog, base_dir)
        if args.target == "sync":
            lock = sync_pinned_sources(
                snapshot_id=catalog.catalog_id,
                manifest_digest=catalog.manifest_digest,
                sources=catalog.sources,
                source_root=source_root,
            )
            _print_json(lock)
        validation = validate_skill_catalog(catalog, source_root)
        _print_json(validation.to_dict())
        return 0 if validation.ok else 1
    if action == "corpus":
        if args.target not in (None, "verify", "sync", "import-appworld"):
            raise ValueError("eval corpus supports verify, sync, and import-appworld")
        if not args.manifest:
            raise ValueError("eval corpus requires --manifest")
        if args.target == "import-appworld":
            payload = write_appworld_manifest(
                _resolve(base_dir, args.manifest),
                dataset=args.dataset,
                split=args.split,
                source_commit=args.source_commit,
                limit=args.limit,
            )
            _print_json(
                {
                    "corpus_id": payload["corpus_id"],
                    "manifest": str(_resolve(base_dir, args.manifest)),
                    "tasks": len(payload["tasks"]),
                }
            )
            return 0
        corpus = load_evaluation_corpus(_resolve(base_dir, args.manifest))
        source_root = corpus_source_root(corpus, base_dir)
        if args.target == "sync":
            lock = sync_corpus_sources(corpus, source_root)
            _print_json(lock)
        result = validate_evaluation_corpus(corpus, source_root=source_root)
        _print_json(result.to_dict())
        return 0 if result.ok else 1

    memory = MemoryStore(resolve_memory_path(config, base_dir))
    runner = EvaluationExperimentRunner(memory, base_dir=base_dir)
    try:
        if action == "iterate":
            if not args.skill_path:
                raise ValueError("eval iterate requires --skill-path")
            if config.agent is None:
                raise ValueError("eval iterate requires an agent LLM configuration")
            llm = create_llm_client(
                provider=config.agent.llm_provider,
                model=config.agent.llm_model,
                api_key=os.environ.get(config.agent.api_key_env),
                base_url=config.agent.llm_base_url,
                temperature=0,
            )
            result = await _stage_candidate_from_evaluation(
                args=args,
                base_dir=base_dir,
                memory=memory,
                llm=llm,
            )
            _print_json({"iteration": result})
            return 0 if result["candidate_experiment_id"] else 1
        if action == "run":
            if not args.manifest:
                raise ValueError("eval run requires --manifest")
            corpus = load_evaluation_corpus(_resolve(base_dir, args.manifest))
            if config.agent is None:
                raise ValueError("eval run requires an agent LLM configuration")
            validation = validate_evaluation_corpus(
                corpus, source_root=corpus_source_root(corpus, base_dir)
            )
            if not validation.ok:
                _print_json(validation.to_dict())
                return 1
            skill_name, variants = _variants(args, base_dir, memory)
            if args.promote_on_success and not (args.experiment_id or args.candidate_path):
                raise ValueError(
                    "--promote-on-success requires --experiment-id or --candidate-path"
                )
            if args.iterate_on_failure and (args.experiment_id or args.candidate_path):
                raise ValueError(
                    "--iterate-on-failure starts from a parent Skill, not an existing candidate"
                )
            llm = create_llm_client(
                provider=config.agent.llm_provider,
                model=config.agent.llm_model,
                api_key=os.environ.get(config.agent.api_key_env),
                base_url=config.agent.llm_base_url,
                temperature=0,
            )
            model_ref = {
                "provider": config.agent.llm_provider,
                "model": config.agent.llm_model,
                "temperature": 0,
                "api_key_env": config.agent.api_key_env,
            }
            if corpus.adapter == "appworld":
                registry = BenchmarkAdapterRegistry()
                registry.register(FixtureBenchmarkAdapter())
                registry.register(AppWorldBenchmarkAdapter())
                runner = EvaluationExperimentRunner(memory, base_dir=base_dir, registry=registry)
                executor = AppWorldLLMBenchmarkExecutor(llm)
            else:
                executor = LiveLLMBenchmarkExecutor(llm)
            summary = await runner.run(
                corpus=corpus,
                split=args.split,
                variants=variants,
                trials=args.trials,
                executor=executor,
                model_ref=model_ref,
                skill_name=skill_name,
                source_execution_ids=[],
                experiment_id=args.experiment_id,
            )
            _print_json(summary.to_dict())
            if summary.status == "validated" and args.promote_on_success:
                args.experiment_id = summary.experiment_id
                return _promote(memory, base_dir, args)
            if summary.status != "validated" and args.iterate_on_failure:
                _print_json(
                    {
                        "iteration": await _stage_candidate_from_evaluation(
                            args=args,
                            base_dir=base_dir,
                            memory=memory,
                            llm=llm,
                        )
                    }
                )
            return 0 if summary.status == "validated" else 1
        if action == "replay":
            if not args.experiment_id:
                raise ValueError("eval replay requires --experiment-id")
            decision = runner.replay(args.experiment_id)
            _print_json(decision.to_dict())
            return 0 if decision.promotable else 1
        if action == "report":
            if not args.experiment_id:
                raise ValueError("eval report requires --experiment-id")
            extension = {"json": "summary.json", "junit": "junit.xml", "markdown": "summary.md"}
            if args.format not in extension:
                raise ValueError("eval report --format must be json, junit, or markdown")
            report = runner._artifact_dir(args.experiment_id) / extension[args.format]
            if not report.is_file():
                raise ValueError(f"Evaluation report does not exist: {report}")
            print(report.read_text(encoding="utf-8"), end="")
            return 0
        if action == "promote":
            return _promote(memory, base_dir, args)
        if action == "rollback":
            return _rollback(memory, base_dir, args)
        raise ValueError(
            "eval supports sources, corpus, run, iterate, replay, report, promote, and rollback"
        )
    finally:
        memory.close()
