"""Persistence for delayed probe attempts, leases, and observations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class ProbeStoreMixin:
    def list_evidence_observations(self, binding_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evidence_observations WHERE binding_id = ? ORDER BY id",
                (binding_id,),
            ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            payload["payload"] = self._json_load_dict(payload.pop("payload_json", ""))
            results.append(payload)
        return results

    def count_probe_attempts(self, binding_id: str, horizon_id: str, probe_revision: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS count FROM probe_attempts
                   WHERE binding_id = ? AND horizon_id = ? AND probe_revision = ?""",
                (binding_id, horizon_id, probe_revision),
            ).fetchone()
        return int(row["count"] if row else 0)

    def acquire_evidence_lease(
        self,
        *,
        binding_id: str,
        horizon_id: str,
        probe_revision: str,
        owner: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        leased_until = now + timedelta(seconds=max(1, lease_seconds))
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO evidence_horizon_leases
                   (binding_id, horizon_id, probe_revision, owner, leased_until,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(binding_id, horizon_id, probe_revision) DO UPDATE SET
                     owner = excluded.owner,
                     leased_until = excluded.leased_until,
                     updated_at = excluded.updated_at
                   WHERE evidence_horizon_leases.leased_until <= ?
                      OR evidence_horizon_leases.owner = excluded.owner""",
                (
                    binding_id,
                    horizon_id,
                    probe_revision,
                    owner,
                    leased_until.isoformat(),
                    self._now(),
                    self._now(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def release_evidence_lease(
        self, *, binding_id: str, horizon_id: str, probe_revision: str, owner: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """DELETE FROM evidence_horizon_leases
                   WHERE binding_id = ? AND horizon_id = ? AND probe_revision = ? AND owner = ?""",
                (binding_id, horizon_id, probe_revision, owner),
            )
            self._conn.commit()

    def record_probe_attempt(
        self,
        *,
        binding_id: str,
        horizon_id: str,
        probe_revision: str,
        status: str,
        attempt_number: int = 1,
        payload: dict[str, Any] | None = None,
        error: str = "",
        lease_owner: str = "",
        started_at: str = "",
        completed_at: str = "",
    ) -> int:
        if self.get_evidence_binding(binding_id) is None:
            raise ValueError(f"Unknown evidence binding: {binding_id}")
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO probe_attempts
                   (binding_id, horizon_id, probe_revision, attempt_number, status,
                    payload_json, error, lease_owner, started_at, completed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    binding_id,
                    horizon_id,
                    probe_revision,
                    max(1, attempt_number),
                    status,
                    self._json_dump(payload or {}),
                    error,
                    lease_owner,
                    started_at,
                    completed_at,
                    self._now(),
                ),
            )
            self._conn.commit()
            return (cursor.lastrowid or 0) if cursor.rowcount > 0 else 0

    def record_evidence_observation(
        self,
        *,
        binding_id: str,
        horizon_id: str,
        probe_revision: str,
        observation_kind: str,
        payload: dict[str, Any],
        observed_at: str,
        evidence_ref: str = "",
    ) -> int:
        if self.get_evidence_binding(binding_id) is None:
            raise ValueError(f"Unknown evidence binding: {binding_id}")
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO evidence_observations
                   (binding_id, horizon_id, probe_revision, observation_kind, payload_json,
                    evidence_ref, observed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    binding_id,
                    horizon_id,
                    probe_revision,
                    observation_kind,
                    self._json_dump(payload),
                    evidence_ref,
                    observed_at,
                    self._now(),
                ),
            )
            self._conn.commit()
            return (cursor.lastrowid or 0) if cursor.rowcount > 0 else 0
