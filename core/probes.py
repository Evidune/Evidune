"""Read-only evidence probes and deterministic delayed-evaluation scheduler."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from core.probe_registry import (
    EvaluatorDefinition,
    EvaluatorRegistry,
    ProbeDefinition,
    ProbeRegistry,
)
from memory.store import MemoryStore

__all__ = [
    "EvaluatorDefinition",
    "EvaluatorRegistry",
    "ProbeDefinition",
    "ProbeRegistry",
    "ProbeRunSummary",
    "ProbeScheduler",
]


@dataclass(frozen=True)
class ProbeRunSummary:
    scanned: int = 0
    observed: int = 0
    evaluated: int = 0
    retried: int = 0
    invalid: int = 0
    leased_elsewhere: int = 0


class ProbeScheduler:
    def __init__(
        self,
        memory: MemoryStore,
        probes: ProbeRegistry,
        evaluators: EvaluatorRegistry,
        *,
        worker_id: str = "",
    ) -> None:
        self.memory = memory
        self.probes = probes
        self.evaluators = evaluators
        self.worker_id = worker_id or f"probe-worker-{uuid.uuid4().hex[:12]}"

    async def run_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> ProbeRunSummary:
        now = now or datetime.now(timezone.utc)
        counts = {
            "scanned": 0,
            "observed": 0,
            "evaluated": 0,
            "retried": 0,
            "invalid": 0,
            "leased_elsewhere": 0,
        }
        bindings = self.memory.list_evidence_bindings(
            statuses=["committed", "scheduled", "observing", "retrying", "partially_matured"],
            limit=limit,
        )
        for binding in bindings:
            counts["scanned"] += 1
            if binding["status"] == "committed":
                self.memory.transition_evidence_binding(binding["id"], "scheduled")
                binding["status"] = "scheduled"
            plan = binding.get("observation_plan") or {}
            horizons = [item for item in plan.get("horizons", []) if isinstance(item, dict)]
            if not horizons:
                self.memory.transition_evidence_binding(
                    binding["id"], "invalid", "observation plan has no horizons"
                )
                counts["invalid"] += 1
                continue
            for horizon in horizons:
                try:
                    due = _is_due(horizon.get("due_at"), now)
                except ValueError as exc:
                    self._invalidate(binding, str(exc), counts)
                    break
                if not due:
                    continue
                horizon_id = str(horizon.get("id") or "")
                probe_id = str(horizon.get("probe_id") or plan.get("probe_id") or "")
                revision = str(horizon.get("probe_revision") or plan.get("probe_revision") or "")
                evaluator_id = str(horizon.get("evaluator_id") or plan.get("evaluator_id") or "")
                evaluator_revision = str(
                    horizon.get("evaluator_revision") or plan.get("evaluator_revision") or ""
                )
                if (
                    not horizon_id
                    or not probe_id
                    or not revision
                    or not evaluator_id
                    or not evaluator_revision
                ):
                    self._invalidate(binding, "horizon lacks probe or evaluator identity", counts)
                    break
                observed = {
                    (item["horizon_id"], item["probe_revision"])
                    for item in self.memory.list_evidence_observations(binding["id"])
                }
                if (horizon_id, revision) in observed:
                    continue
                acquired = self.memory.acquire_evidence_lease(
                    binding_id=binding["id"],
                    horizon_id=horizon_id,
                    probe_revision=revision,
                    owner=self.worker_id,
                    lease_seconds=int(horizon.get("lease_seconds") or 60),
                    now=now,
                )
                if not acquired:
                    counts["leased_elsewhere"] += 1
                    continue
                try:
                    if binding["status"] in {"scheduled", "retrying", "partially_matured"}:
                        self.memory.transition_evidence_binding(binding["id"], "observing")
                        binding["status"] = "observing"
                    attempt = (
                        self.memory.count_probe_attempts(binding["id"], horizon_id, revision) + 1
                    )
                    started_at = now.isoformat()
                    try:
                        arguments = _resolve_arguments(
                            dict(horizon.get("arguments") or plan.get("arguments") or {}),
                            binding,
                        )
                        payload, actual_revision = await self.probes.execute(probe_id, arguments)
                        if actual_revision != revision:
                            raise ValueError(
                                f"Probe revision mismatch: plan={revision}, runtime={actual_revision}"
                            )
                        results = self.evaluators.evaluate(
                            evaluator_id,
                            binding,
                            payload,
                            expected_revision=evaluator_revision,
                        )
                    except Exception as exc:
                        self.memory.record_probe_attempt(
                            binding_id=binding["id"],
                            horizon_id=horizon_id,
                            probe_revision=revision,
                            attempt_number=attempt,
                            status="failed",
                            error=str(exc),
                            lease_owner=self.worker_id,
                            started_at=started_at,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                        )
                        maximum = int(horizon.get("max_attempts") or 3)
                        if attempt >= maximum:
                            self.memory.transition_evidence_binding(
                                binding["id"], "invalid", f"probe exhausted retries: {exc}"
                            )
                            binding["status"] = "invalid"
                            counts["invalid"] += 1
                        else:
                            self.memory.transition_evidence_binding(
                                binding["id"], "retrying", str(exc)
                            )
                            binding["status"] = "retrying"
                            counts["retried"] += 1
                    else:
                        self.memory.record_probe_attempt(
                            binding_id=binding["id"],
                            horizon_id=horizon_id,
                            probe_revision=revision,
                            attempt_number=attempt,
                            status="completed",
                            payload=payload,
                            lease_owner=self.worker_id,
                            started_at=started_at,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                        )
                        self.memory.record_evidence_observation(
                            binding_id=binding["id"],
                            horizon_id=horizon_id,
                            probe_revision=revision,
                            observation_kind=str(horizon.get("kind") or "external_state"),
                            payload=payload,
                            evidence_ref=f"probe://{probe_id}/{revision}",
                            observed_at=now.isoformat(),
                        )
                        counts["observed"] += 1
                        for result in results:
                            self.memory.record_evaluation_result(
                                replace(
                                    result,
                                    metadata={
                                        **result.metadata,
                                        "binding_id": binding["id"],
                                        "horizon_id": horizon_id,
                                    },
                                ).to_dict()
                            )
                        all_observed = {
                            item["horizon_id"]
                            for item in self.memory.list_evidence_observations(binding["id"])
                        }
                        required = {str(item.get("id") or "") for item in horizons}
                        next_status = (
                            "evaluated" if required <= all_observed else "partially_matured"
                        )
                        self.memory.transition_evidence_binding(binding["id"], next_status)
                        binding["status"] = next_status
                        if next_status == "evaluated":
                            counts["evaluated"] += 1
                    if binding["status"] in {"evaluated", "invalid", "retrying"}:
                        break
                finally:
                    self.memory.release_evidence_lease(
                        binding_id=binding["id"],
                        horizon_id=horizon_id,
                        probe_revision=revision,
                        owner=self.worker_id,
                    )
        return ProbeRunSummary(**counts)

    def _invalidate(self, binding: dict[str, Any], reason: str, counts: dict[str, int]) -> None:
        current = self.memory.get_evidence_binding(binding["id"])
        if current and current["status"] in {
            "scheduled",
            "observing",
            "retrying",
            "partially_matured",
        }:
            self.memory.transition_evidence_binding(binding["id"], "invalid", reason)
            counts["invalid"] += 1


def _is_due(raw: Any, now: datetime) -> bool:
    if not raw:
        return True
    try:
        due = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValueError(f"Invalid horizon due_at: {raw}") from exc
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= now


def _resolve_arguments(arguments: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    replacements = {
        "$entity_id": binding["entity_id"],
        "$entity_type": binding["entity_type"],
        "$execution_id": binding["execution_id"],
    }
    return {
        key: replacements.get(value, value) if isinstance(value, str) else value
        for key, value in arguments.items()
    }
