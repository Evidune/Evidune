"""Persistence for execution-to-entity evidence commitments."""

from __future__ import annotations

import uuid
from typing import Any

from memory.evaluation_store import ATTRIBUTION_GRADES

BINDING_TRANSITIONS = {
    "committed": {"scheduled", "cancelled", "invalid"},
    "scheduled": {"observing", "cancelled", "expired", "invalid"},
    "observing": {"retrying", "partially_matured", "evaluated", "censored", "invalid"},
    "retrying": {"observing", "expired", "censored", "invalid", "cancelled"},
    "partially_matured": {"observing", "evaluated", "censored", "invalid"},
    "evaluated": set(),
    "expired": set(),
    "censored": set(),
    "invalid": set(),
    "cancelled": set(),
}


class EvidenceStoreMixin:
    def create_evidence_binding(
        self,
        *,
        execution_id: int,
        skill_name: str,
        entity_type: str,
        entity_id: str,
        skill_version: str = "",
        intervention: dict[str, Any] | None = None,
        expected_state: dict[str, Any] | None = None,
        forbidden_state: dict[str, Any] | None = None,
        observation_plan: dict[str, Any] | None = None,
        attribution_policy: str = "unknown",
        minimum_evidence_grade: str = "unknown",
        probe_digest: str = "",
        evaluator_digest: str = "",
        contract_digest: str = "",
        binding_id: str = "",
    ) -> str:
        execution = self.get_skill_executions_by_id(execution_id)
        if execution is None:
            raise ValueError(f"Unknown execution_id: {execution_id}")
        if skill_name != execution["skill_name"]:
            raise ValueError("Evidence binding Skill name does not match its execution")
        if execution["skill_version"] and skill_version != execution["skill_version"]:
            raise ValueError("Evidence binding Skill version does not match its execution")
        if not entity_type.strip() or not entity_id.strip():
            raise ValueError("Evidence binding requires entity_type and entity_id")
        if attribution_policy not in ATTRIBUTION_GRADES:
            raise ValueError(f"Invalid attribution policy: {attribution_policy}")
        if minimum_evidence_grade not in ATTRIBUTION_GRADES:
            raise ValueError(f"Invalid minimum evidence grade: {minimum_evidence_grade}")
        binding_id = binding_id or f"evb_{uuid.uuid4().hex}"
        now = self._now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO evidence_bindings
                   (id, execution_id, skill_name, skill_version, entity_type, entity_id,
                    intervention_json, expected_state_json, forbidden_state_json,
                    observation_plan_json, attribution_policy, minimum_evidence_grade,
                    probe_digest, evaluator_digest, contract_digest, status, reason,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', '', ?, ?)""",
                (
                    binding_id,
                    execution_id,
                    skill_name,
                    skill_version,
                    entity_type,
                    entity_id,
                    self._json_dump(intervention or {}),
                    self._json_dump(expected_state or {}),
                    self._json_dump(forbidden_state or {}),
                    self._json_dump(observation_plan or {}),
                    attribution_policy,
                    minimum_evidence_grade,
                    probe_digest,
                    evaluator_digest,
                    contract_digest,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return binding_id

    def transition_evidence_binding(self, binding_id: str, status: str, reason: str = "") -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM evidence_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Unknown evidence binding: {binding_id}")
            current = row["status"]
            if status not in BINDING_TRANSITIONS.get(current, set()):
                raise ValueError(f"Invalid evidence binding transition: {current} -> {status}")
            self._conn.execute(
                "UPDATE evidence_bindings SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                (status, reason, self._now(), binding_id),
            )
            self._conn.commit()

    def get_evidence_binding(self, binding_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evidence_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        for key in ("intervention", "expected_state", "forbidden_state", "observation_plan"):
            payload[key] = self._json_load_dict(payload.pop(f"{key}_json", ""))
        return payload

    def list_evidence_bindings(
        self, *, statuses: list[str] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT id FROM evidence_bindings"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY created_at LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            binding for row in rows if (binding := self.get_evidence_binding(row["id"])) is not None
        ]
