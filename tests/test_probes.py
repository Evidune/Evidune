from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.probes import (
    EvaluatorDefinition,
    EvaluatorRegistry,
    ProbeDefinition,
    ProbeRegistry,
    ProbeScheduler,
)
from memory.store import MemoryStore
from skills.governance import EvaluationResult


def _execution_and_binding(store: MemoryStore, *, max_attempts: int = 3) -> tuple[int, str]:
    execution_id = store.record_execution(
        skill_name="publisher",
        skill_version="2.0",
        user_input="publish",
        assistant_output="published",
    )
    binding_id = store.create_evidence_binding(
        execution_id=execution_id,
        skill_name="publisher",
        skill_version="2.0",
        entity_type="article",
        entity_id="article-42",
        observation_plan={
            "probe_id": "article_status",
            "probe_revision": "v1",
            "evaluator_id": "published_predicate",
            "evaluator_revision": "v1",
            "horizons": [
                {
                    "id": "immediate",
                    "arguments": {"article_id": "$entity_id"},
                    "max_attempts": max_attempts,
                }
            ],
        },
        attribution_policy="direct",
        minimum_evidence_grade="direct",
    )
    return execution_id, binding_id


def _evaluator(binding, payload):
    published = payload["status"] == "published"
    return EvaluationResult(
        evaluator_id="placeholder",
        evaluator_revision="placeholder",
        evaluator_type="predicate",
        verdict="pass" if published else "fail",
        dimensions={"published": published},
        failure_modes=[] if published else ["not_published"],
        attribution_grade="direct",
    )


@pytest.mark.asyncio
async def test_probe_scheduler_closes_binding_without_duplicate_observations(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    execution_id, binding_id = _execution_and_binding(store)
    probes = ProbeRegistry()

    async def status(article_id):
        assert article_id == "article-42"
        return {"status": "published"}

    probes.register(
        ProbeDefinition(
            "article_status",
            "v1",
            status,
            allowed_arguments={"article_id"},
            output_schema={
                "required": ["status"],
                "properties": {"status": {"type": "string"}},
            },
        )
    )
    evaluators = EvaluatorRegistry()
    evaluators.register(EvaluatorDefinition("published_predicate", "v1", "predicate", _evaluator))
    scheduler = ProbeScheduler(store, probes, evaluators, worker_id="worker-a")

    first = await scheduler.run_due(now=datetime.now(timezone.utc))
    second = await scheduler.run_due(now=datetime.now(timezone.utc))

    assert first.observed == 1
    assert first.evaluated == 1
    assert second.observed == 0
    assert store.get_evidence_binding(binding_id)["status"] == "evaluated"
    assert len(store.list_evidence_observations(binding_id)) == 1
    results = store.list_evaluation_results(execution_id=execution_id)
    assert results[0]["verdict"] == "pass"
    assert results[0]["score"] is None
    store.close()


@pytest.mark.asyncio
async def test_probe_failure_retries_without_negative_skill_evidence(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    execution_id, binding_id = _execution_and_binding(store, max_attempts=2)
    probes = ProbeRegistry()

    async def unavailable(article_id):
        raise ConnectionError("analytics unavailable")

    probes.register(
        ProbeDefinition("article_status", "v1", unavailable, allowed_arguments={"article_id"})
    )
    evaluators = EvaluatorRegistry()
    evaluators.register(EvaluatorDefinition("published_predicate", "v1", "predicate", _evaluator))
    scheduler = ProbeScheduler(store, probes, evaluators, worker_id="worker-a")

    first = await scheduler.run_due()
    second = await scheduler.run_due()

    assert first.retried == 1
    assert second.invalid == 1
    assert store.get_evidence_binding(binding_id)["status"] == "invalid"
    assert store.list_evaluation_results(execution_id=execution_id) == []
    store.close()


@pytest.mark.asyncio
async def test_probe_registry_rejects_write_capability_and_unlisted_arguments():
    probes = ProbeRegistry()
    with pytest.raises(ValueError, match="read-only"):
        probes.register(ProbeDefinition("writer", "v1", lambda: {}, capability="write"))
    probes.register(ProbeDefinition("reader", "v1", lambda: {}, allowed_arguments=set()))
    with pytest.raises(ValueError, match="already registered"):
        probes.register(ProbeDefinition("reader", "v2", lambda: {}))
    with pytest.raises(ValueError, match="not allowlisted"):
        await probes.execute("reader", {"command": "delete"})

    typed = ProbeRegistry()
    typed.register(
        ProbeDefinition(
            "count",
            "v1",
            lambda: {"count": True},
            output_schema={"properties": {"count": {"type": "integer"}}},
        )
    )
    with pytest.raises(ValueError, match="wrong type"):
        await typed.execute("count", {})


@pytest.mark.asyncio
async def test_evaluator_revision_drift_is_retryable_infrastructure_failure(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    execution_id, _ = _execution_and_binding(store)
    probes = ProbeRegistry()
    probes.register(
        ProbeDefinition(
            "article_status",
            "v1",
            lambda article_id: {"status": "published"},
            allowed_arguments={"article_id"},
        )
    )
    evaluators = EvaluatorRegistry()
    evaluators.register(EvaluatorDefinition("published_predicate", "v2", "predicate", _evaluator))

    summary = await ProbeScheduler(store, probes, evaluators).run_due()

    assert summary.retried == 1
    assert store.list_evaluation_results(execution_id=execution_id) == []
    store.close()


def test_evidence_lease_is_exclusive_across_workers(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _, binding_id = _execution_and_binding(store)
    now = datetime.now(timezone.utc)

    assert store.acquire_evidence_lease(
        binding_id=binding_id,
        horizon_id="immediate",
        probe_revision="v1",
        owner="worker-a",
        now=now,
    )
    assert not store.acquire_evidence_lease(
        binding_id=binding_id,
        horizon_id="immediate",
        probe_revision="v1",
        owner="worker-b",
        now=now,
    )
    store.close()
