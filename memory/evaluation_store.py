"""Persistence for immutable contracts and typed evaluation results."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

VERDICTS = {"pass", "fail", "inconclusive", "censored", "invalid"}
ATTRIBUTION_GRADES = {"direct", "controlled", "supported", "observational", "unknown"}


def digest_payload(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class EvaluationStoreMixin:
    def record_contract_snapshot(
        self,
        *,
        contract_kind: str,
        contract: dict[str, Any],
        contract_version: str = "",
        source: str = "",
        digest: str = "",
    ) -> str:
        if contract_kind not in {"execution", "outcome", "governance", "benchmark"}:
            raise ValueError(f"Unsupported contract kind: {contract_kind}")
        snapshot_digest = digest or digest_payload(contract)
        with self._lock:
            existing = self._conn.execute(
                "SELECT contract_json FROM evaluation_contract_snapshots WHERE digest = ?",
                (snapshot_digest,),
            ).fetchone()
            encoded = self._json_dump(contract)
            if existing and self._json_load_dict(existing["contract_json"]) != contract:
                raise ValueError("Contract digest collision or mismatched immutable snapshot")
            self._conn.execute(
                """INSERT OR IGNORE INTO evaluation_contract_snapshots
                   (digest, contract_kind, contract_version, contract_json, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot_digest, contract_kind, contract_version, encoded, source, self._now()),
            )
            self._conn.commit()
        return snapshot_digest

    def get_contract_snapshot(self, digest: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evaluation_contract_snapshots WHERE digest = ?", (digest,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["contract"] = self._json_load_dict(payload.pop("contract_json", ""))
        return payload

    def record_evaluation_result(self, result: dict[str, Any]) -> str:
        verdict = str(result.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            raise ValueError(f"Invalid evaluation verdict: {verdict!r}")
        execution_id = int(result.get("execution_id") or 0)
        execution = self.get_skill_executions_by_id(execution_id) if execution_id > 0 else None
        if execution is None:
            raise ValueError(f"Unknown execution_id: {execution_id}")
        skill_name = str(result.get("skill_name") or execution["skill_name"])
        skill_version = str(result.get("skill_version") or execution["skill_version"])
        if skill_name != execution["skill_name"]:
            raise ValueError("Evaluation Skill name does not match its execution")
        if execution["skill_version"] and skill_version != execution["skill_version"]:
            raise ValueError("Evaluation Skill version does not match its execution")
        attribution = str(result.get("attribution_grade") or "unknown").strip().lower()
        if attribution not in ATTRIBUTION_GRADES:
            raise ValueError(f"Invalid attribution grade: {attribution!r}")
        evaluator_id = str(result.get("evaluator_id") or "").strip()
        evaluator_revision = str(result.get("evaluator_revision") or "").strip()
        evaluator_type = str(result.get("evaluator_type") or "").strip()
        if not evaluator_id or not evaluator_revision or not evaluator_type:
            raise ValueError("Evaluation result requires evaluator id, revision, and type")
        result_uid = str(result.get("result_uid") or f"evr_{uuid.uuid4().hex}")
        with self._lock:
            self._conn.execute(
                """INSERT INTO evaluation_results
                   (result_uid, execution_id, skill_name, skill_version, evaluator_id,
                    evaluator_revision, evaluator_type, contract_digest, verdict, score,
                    uncertainty, dimensions_json, failure_modes_json, evidence_refs_json,
                    hard_gate_failures_json, attribution_grade, reasoning, metadata_json,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result_uid,
                    execution_id,
                    skill_name,
                    skill_version,
                    evaluator_id,
                    evaluator_revision,
                    evaluator_type,
                    str(result.get("contract_digest") or ""),
                    verdict,
                    result.get("score"),
                    str(result.get("uncertainty") or "unknown"),
                    self._json_dump(result.get("dimensions") or {}),
                    self._json_dump(result.get("failure_modes") or []),
                    self._json_dump(result.get("evidence_refs") or []),
                    self._json_dump(result.get("hard_gate_failures") or []),
                    attribution,
                    str(result.get("reasoning") or ""),
                    self._json_dump(result.get("metadata") or {}),
                    self._now(),
                ),
            )
            self._conn.commit()
        return result_uid

    def list_evaluation_results(
        self,
        *,
        execution_id: int | None = None,
        skill_name: str = "",
        skill_version: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("execution_id", execution_id),
            ("skill_name", skill_name),
            ("skill_version", skill_version),
        ):
            if value not in (None, ""):
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM evaluation_results {where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            for key in ("dimensions", "metadata"):
                payload[key] = self._json_load_dict(payload.pop(f"{key}_json", ""))
            for key in ("failure_modes", "evidence_refs", "hard_gate_failures"):
                payload[key] = self._json_load_list(payload.pop(f"{key}_json", ""))
            results.append(payload)
        return results
