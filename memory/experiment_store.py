"""Persistence for immutable Skill candidates and benchmark trials."""

from __future__ import annotations

import uuid
from typing import Any

EXPERIMENT_TRANSITIONS = {
    "candidate": {"validated", "rejected", "inconclusive"},
    "validated": {"shadow", "canary", "promoted", "rejected"},
    "shadow": {"canary", "promoted", "rejected"},
    "canary": {"promoted", "rejected"},
    "promoted": {"rolled_back"},
    "rejected": set(),
    "inconclusive": set(),
    "rolled_back": set(),
}


class ExperimentStoreMixin:
    def create_skill_experiment(
        self,
        *,
        skill_name: str,
        parent_version: str,
        parent_digest: str,
        parent_content: str,
        candidate_version: str,
        candidate_digest: str,
        candidate_content: str,
        source_execution_ids: list[int],
        corpus_id: str = "",
        split: str = "",
        model_ref: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        experiment_id: str = "",
    ) -> str:
        source_execution_ids = sorted({int(value) for value in source_execution_ids})
        for execution_id in source_execution_ids:
            execution = self.get_skill_executions_by_id(execution_id)
            if execution is None:
                raise ValueError(f"Unknown source execution_id: {execution_id}")
            if execution["skill_name"] != skill_name:
                raise ValueError(
                    "Source execution skill does not match experiment skill: "
                    f"{execution['skill_name']} != {skill_name}"
                )
        experiment_id = experiment_id or f"exp_{uuid.uuid4().hex}"
        now = self._now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO skill_version_experiments
                   (id, skill_name, status, parent_version, parent_digest, parent_content,
                    candidate_version, candidate_digest, candidate_content,
                    source_execution_ids_json, corpus_id, split, model_ref_json, budget_json,
                    policy_json, evidence_json, reason, created_at, updated_at)
                   VALUES (?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '', ?, ?)""",
                (
                    experiment_id,
                    skill_name,
                    parent_version,
                    parent_digest,
                    parent_content,
                    candidate_version,
                    candidate_digest,
                    candidate_content,
                    self._json_dump(source_execution_ids),
                    corpus_id,
                    split,
                    self._json_dump(model_ref or {}),
                    self._json_dump(budget or {}),
                    self._json_dump(policy or {}),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return experiment_id

    def transition_skill_experiment(
        self,
        experiment_id: str,
        status: str,
        *,
        reason: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT status, evidence_json FROM skill_version_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Unknown Skill experiment: {experiment_id}")
            current = row["status"]
            if status not in EXPERIMENT_TRANSITIONS.get(current, set()):
                raise ValueError(f"Invalid Skill experiment transition: {current} -> {status}")
            merged = self._json_load_dict(row["evidence_json"])
            merged.update(evidence or {})
            self._conn.execute(
                """UPDATE skill_version_experiments
                   SET status = ?, reason = ?, evidence_json = ?, updated_at = ? WHERE id = ?""",
                (status, reason, self._json_dump(merged), self._now(), experiment_id),
            )
            self._conn.commit()

    def bind_skill_experiment_validation(
        self,
        experiment_id: str,
        *,
        corpus_id: str,
        split: str,
        model_ref: dict[str, Any],
        budget: dict[str, Any],
        policy: dict[str, Any],
    ) -> None:
        """Bind a staged candidate to its first immutable validation run."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, corpus_id, split FROM skill_version_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Unknown Skill experiment: {experiment_id}")
            if row["status"] != "candidate":
                raise ValueError("Only a candidate experiment can be bound for validation")
            if row["corpus_id"] and row["corpus_id"] != corpus_id:
                raise ValueError("Candidate experiment is already bound to another corpus")
            if row["split"] and row["split"] != split:
                raise ValueError("Candidate experiment is already bound to another split")
            self._conn.execute(
                """UPDATE skill_version_experiments
                   SET corpus_id = ?, split = ?, model_ref_json = ?, budget_json = ?,
                       policy_json = ?, updated_at = ? WHERE id = ?""",
                (
                    corpus_id,
                    split,
                    self._json_dump(model_ref),
                    self._json_dump(budget),
                    self._json_dump(policy),
                    self._now(),
                    experiment_id,
                ),
            )
            self._conn.commit()

    def get_skill_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM skill_version_experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        for key in ("model_ref", "budget", "policy", "evidence"):
            payload[key] = self._json_load_dict(payload.pop(f"{key}_json", ""))
        payload["source_execution_ids"] = self._json_load_list(
            payload.pop("source_execution_ids_json", "")
        )
        return payload

    def list_skill_experiments(
        self, skill_name: str, *, status: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        query = "SELECT id FROM skill_version_experiments WHERE skill_name = ?"
        params: list[Any] = [skill_name]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            experiment
            for row in rows
            if (experiment := self.get_skill_experiment(row["id"])) is not None
        ]

    def record_experiment_trial(
        self,
        *,
        experiment_id: str,
        task_ref: str,
        split: str,
        variant: str,
        trial_number: int,
        status: str,
        execution_id: int | None = None,
        classification: str = "",
        result: dict[str, Any] | None = None,
        started_at: str = "",
        completed_at: str = "",
        trial_id: str = "",
    ) -> str:
        if self.get_skill_experiment(experiment_id) is None:
            raise ValueError(f"Unknown Skill experiment: {experiment_id}")
        if execution_id is not None and self.get_skill_executions_by_id(execution_id) is None:
            raise ValueError(f"Unknown trial execution_id: {execution_id}")
        trial_id = trial_id or f"trial_{uuid.uuid4().hex}"
        with self._lock:
            self._conn.execute(
                """INSERT INTO skill_experiment_trials
                   (id, experiment_id, task_ref, split, variant, trial_number, execution_id,
                    status, classification, result_json, started_at, completed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trial_id,
                    experiment_id,
                    task_ref,
                    split,
                    variant,
                    max(1, trial_number),
                    execution_id,
                    status,
                    classification,
                    self._json_dump(result or {}),
                    started_at,
                    completed_at,
                    self._now(),
                ),
            )
            self._conn.commit()
        return trial_id

    def list_experiment_trials(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM skill_experiment_trials
                   WHERE experiment_id = ? ORDER BY task_ref, variant, trial_number""",
                (experiment_id,),
            ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            payload["result"] = self._json_load_dict(payload.pop("result_json", ""))
            results.append(payload)
        return results
