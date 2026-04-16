"""SQLite-backed persistent memory store.

This module exposes the `MemoryStore` class — the single entrypoint for
all persistence in aiflay. Its SQL DDL and migrations live in
`memory/schema.py`, and row→object conversion lives in `memory/rows.py`,
so this file stays focused on the public API surface.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from memory.rows import row_to_execution, row_to_fact, row_to_iteration_run
from memory.schema import init_schema
from memory.store_models import Fact, Message  # noqa: F401 — re-exported

__all__ = ["MemoryStore", "Fact", "Message"]

_PLAN_STATUSES = {"pending", "in_progress", "completed"}
_CONVERSATION_MODES = {"plan", "execute"}
_EMERGED_SKILL_STATUSES = {"active", "pending_review", "disabled", "rolled_back"}


class MemoryStore:
    """SQLite-backed cross-session memory.

    Stores conversations, messages, facts (namespaced), skill
    executions, and emerged-skill metadata. All state for a single
    aiflay instance lives in one sqlite file — simple, auditable,
    portable.
    """

    def __init__(self, db_path: str | Path = "~/.aiflay/memory.db") -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # WebGateway serves HTTP requests in a background thread while the
        # agent loop runs on the main event-loop thread. Allow the same
        # sqlite connection to be shared, then serialize access with an
        # in-process lock to avoid cross-thread ProgrammingError.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        init_schema(self._conn)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _decode_plan(self, raw: str) -> dict | None:
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        explanation = payload.get("explanation", "")
        items = payload.get("items", [])
        if not isinstance(explanation, str) or not isinstance(items, list):
            return None
        return {"explanation": explanation, "items": items}

    def _normalise_plan_items(self, items: list[dict]) -> list[dict[str, str]]:
        normalised: list[dict[str, str]] = []
        in_progress = 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Plan items must be objects with step and status")
            step = item.get("step")
            status = item.get("status")
            if not isinstance(step, str) or not step.strip():
                raise ValueError("Each plan item must include a non-empty step")
            if status not in _PLAN_STATUSES:
                valid = ", ".join(sorted(_PLAN_STATUSES))
                raise ValueError(f"Invalid plan status {status!r}; expected one of {valid}")
            if status == "in_progress":
                in_progress += 1
            normalised.append({"step": step.strip(), "status": status})
        if in_progress > 1:
            raise ValueError("Only one plan item can be in_progress")
        return normalised

    def _normalise_mode(self, mode: str) -> str:
        if mode not in _CONVERSATION_MODES:
            valid = ", ".join(sorted(_CONVERSATION_MODES))
            raise ValueError(f"Invalid conversation mode {mode!r}; expected one of {valid}")
        return mode

    def _json_dump(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _json_load_dict(self, raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _normalise_emerged_skill_status(self, status: str) -> str:
        if status not in _EMERGED_SKILL_STATUSES:
            valid = ", ".join(sorted(_EMERGED_SKILL_STATUSES))
            raise ValueError(f"Invalid emerged skill status {status!r}; expected one of {valid}")
        return status

    def _normalise_iteration_updates(
        self, updates: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        normalised: list[dict[str, Any]] = []
        for update in updates or []:
            if not isinstance(update, dict):
                raise ValueError("Iteration updates must be objects")
            path = update.get("path")
            strategy = update.get("strategy")
            if not isinstance(path, str) or not path.strip():
                raise ValueError("Each iteration update must include a non-empty path")
            if not isinstance(strategy, str) or not strategy.strip():
                raise ValueError("Each iteration update must include a non-empty strategy")
            normalised.append(
                {
                    "path": path,
                    "strategy": strategy,
                    "has_changes": bool(update.get("has_changes", False)),
                }
            )
        return normalised

    # --- Conversation / Message API ---

    def ensure_conversation(
        self, conversation_id: str, channel: str = "", identity: str = ""
    ) -> None:
        """Create a conversation if it doesn't exist."""
        with self._lock:
            now = self._now()
            self._conn.execute(
                """INSERT OR IGNORE INTO conversations
                   (id, channel, identity, mode, created_at, updated_at)
                   VALUES (?, ?, ?, 'execute', ?, ?)""",
                (conversation_id, channel, identity, now, now),
            )
            # Backfill the channel for legacy rows that were created before
            # the caller had the gateway/channel context available.
            if channel:
                self._conn.execute(
                    "UPDATE conversations SET channel = ? WHERE id = ? AND channel = ''",
                    (channel, conversation_id),
                )
            if identity:
                self._conn.execute(
                    "UPDATE conversations SET identity = ? WHERE id = ? AND identity = ''",
                    (identity, conversation_id),
                )
            self._conn.commit()

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        """Store a message in conversation history."""
        with self._lock:
            now = self._now()
            self.ensure_conversation(conversation_id)
            self._conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, now),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            self._conn.commit()

    def get_history(self, conversation_id: str, limit: int = 20) -> list[dict[str, str]]:
        """Get recent messages for a conversation (chronological order)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def trim_history(self, conversation_id: str, keep: int = 100) -> int:
        """Delete old messages beyond the keep limit. Returns number deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            count = cursor.fetchone()["cnt"]
            if count <= keep:
                return 0

            to_delete = count - keep
            self._conn.execute(
                """DELETE FROM messages WHERE id IN (
                    SELECT id FROM messages WHERE conversation_id = ?
                    ORDER BY id ASC LIMIT ?
                )""",
                (conversation_id, to_delete),
            )
            self._conn.commit()
            return to_delete

    def list_conversations(
        self,
        limit: int = 50,
        status: str | None = "active",
        channel: str | None = None,
    ) -> list[dict]:
        """List conversations ordered by most recent activity.

        Defaults to only `active` conversations. Pass status=None for
        everything (incl. archived), or status='archived' for just archived.
        Each row is enriched with message_count + last-message preview.
        """
        where: list[str] = []
        params: list = []
        if status is not None:
            where.append("c.status = ?")
            params.append(status)
        if channel is not None:
            where.append("c.channel = ?")
            params.append(channel)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT c.id, c.channel, c.identity, c.mode, c.plan_json, c.title, c.status,
                           c.created_at, c.updated_at,
                           (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id)
                             AS message_count,
                           (SELECT content FROM messages
                            WHERE conversation_id = c.id
                            ORDER BY id DESC LIMIT 1) AS last_message
                   FROM conversations c
                   {where_clause}
                   ORDER BY c.updated_at DESC
                   LIMIT ?""",
                params,
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            preview = r["last_message"] or ""
            if len(preview) > 120:
                preview = preview[:120] + "…"
            out.append(
                {
                    "id": r["id"],
                    "channel": r["channel"],
                    "identity": r["identity"] or "",
                    "mode": r["mode"] or "execute",
                    "has_plan": bool(r["plan_json"]),
                    "title": r["title"] or "",
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "message_count": r["message_count"],
                    "preview": preview,
                }
            )
        return out

    def get_conversation(self, conversation_id: str) -> dict | None:
        """Get a conversation's metadata (without history)."""
        with self._lock:
            row = self._conn.execute(
                """SELECT id, channel, identity, mode, plan_json, title, status, created_at, updated_at
                   FROM conversations WHERE id = ?""",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        meta = dict(row)
        meta["mode"] = meta.get("mode") or "execute"
        meta["plan"] = self._decode_plan(meta.pop("plan_json", ""))
        return meta

    def set_conversation_title(self, conversation_id: str, title: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def set_conversation_status(self, conversation_id: str, status: str) -> bool:
        """Set status to 'active' or 'archived'."""
        if status not in ("active", "archived"):
            raise ValueError(f"Invalid status: {status!r}")
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def set_conversation_identity(self, conversation_id: str, identity: str) -> bool:
        """Persist the current identity for a conversation."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET identity = ?, updated_at = ? WHERE id = ?",
                (identity, self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def set_conversation_mode(self, conversation_id: str, mode: str) -> bool:
        """Persist the current operating mode for a conversation."""
        normalised = self._normalise_mode(mode)
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET mode = ?, updated_at = ? WHERE id = ?",
                (normalised, self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_conversation_mode(self, conversation_id: str) -> str | None:
        """Return the stored mode for a conversation."""
        with self._lock:
            row = self._conn.execute(
                "SELECT mode FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return row["mode"] or "execute"

    def get_conversation_plan(self, conversation_id: str) -> dict | None:
        """Return the structured plan for a conversation, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT plan_json FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return self._decode_plan(row["plan_json"] or "")

    def update_conversation_plan(
        self,
        conversation_id: str,
        items: list[dict],
        explanation: str = "",
    ) -> bool:
        """Replace the structured plan for a conversation."""
        if not isinstance(explanation, str):
            raise ValueError("Plan explanation must be a string")
        normalised_items = self._normalise_plan_items(items)
        payload = json.dumps(
            {"explanation": explanation.strip(), "items": normalised_items},
            ensure_ascii=False,
        )
        with self._lock:
            self.ensure_conversation(conversation_id)
            cursor = self._conn.execute(
                "UPDATE conversations SET plan_json = ?, updated_at = ? WHERE id = ?",
                (payload, self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def clear_conversation_plan(self, conversation_id: str) -> bool:
        """Clear the current plan for a conversation."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE conversations SET plan_json = '', updated_at = ? WHERE id = ?",
                (self._now(), conversation_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and all its messages + skill executions.

        Returns True if the conversation row existed.
        """
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute(
                "DELETE FROM skill_executions WHERE conversation_id = ?", (conversation_id,)
            )
            cursor = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # --- Facts API (namespaced) ---
    #
    # namespace="" is the global / shared namespace (default).
    # namespace="identity:<name>" isolates one assistant identity's facts.
    # All read/write helpers default to the global namespace for
    # backward compatibility.

    def set_fact(self, key: str, value: str, source: str = "agent", namespace: str = "") -> None:
        """Set or update a persistent fact in the given namespace."""
        with self._lock:
            now = self._now()
            self._conn.execute(
                """INSERT INTO facts (namespace, key, value, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(namespace, key) DO UPDATE SET
                     value = ?, source = ?, updated_at = ?""",
                (namespace, key, value, source, now, now, value, source, now),
            )
            self._conn.commit()

    def get_fact(self, key: str, namespace: str = "") -> str | None:
        """Get a single fact by key from the given namespace."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM facts WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return row["value"] if row else None

    def get_facts(self, prefix: str | None = None, namespace: str | None = "") -> list[Fact]:
        """Get facts in a namespace, optionally filtered by key prefix.

        Pass namespace=None to get facts across ALL namespaces.
        """
        with self._lock:
            if namespace is None:
                if prefix:
                    rows = self._conn.execute(
                        "SELECT * FROM facts WHERE key LIKE ? ORDER BY namespace, key",
                        (f"{prefix}%",),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT * FROM facts ORDER BY namespace, key"
                    ).fetchall()
            elif prefix:
                rows = self._conn.execute(
                    "SELECT * FROM facts WHERE namespace = ? AND key LIKE ? ORDER BY key",
                    (namespace, f"{prefix}%"),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM facts WHERE namespace = ? ORDER BY key", (namespace,)
                ).fetchall()
        return [row_to_fact(r) for r in rows]

    def search_facts(self, query: str, namespace: str | None = "") -> list[Fact]:
        """Search facts by value or key (LIKE).

        namespace="" → only the global namespace
        namespace=None → search across all namespaces
        namespace="x" → only namespace 'x'
        """
        with self._lock:
            if namespace is None:
                rows = self._conn.execute(
                    "SELECT * FROM facts WHERE value LIKE ? OR key LIKE ? ORDER BY updated_at DESC",
                    (f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM facts WHERE namespace = ? AND (value LIKE ? OR key LIKE ?) "
                    "ORDER BY updated_at DESC",
                    (namespace, f"%{query}%", f"%{query}%"),
                ).fetchall()
        return [row_to_fact(r) for r in rows]

    def delete_fact(self, key: str, namespace: str = "") -> bool:
        """Delete a fact. Returns True if it existed."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM facts WHERE namespace = ? AND key = ?", (namespace, key)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # --- Skill Execution API ---

    def record_execution(
        self,
        skill_name: str,
        user_input: str,
        assistant_output: str,
        conversation_id: str | None = None,
        signals: dict | None = None,
        cross_model_score: float | None = None,
        evaluator_reasoning: str | None = None,
    ) -> int:
        """Record a skill execution. Returns the new row id."""
        with self._lock:
            now = self._now()
            cursor = self._conn.execute(
                """INSERT INTO skill_executions
                   (skill_name, conversation_id, user_input, assistant_output,
                    signals_json, cross_model_score, evaluator_reasoning, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill_name,
                    conversation_id,
                    user_input,
                    assistant_output,
                    json.dumps(signals or {}, ensure_ascii=False),
                    cross_model_score,
                    evaluator_reasoning,
                    now,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid or 0

    def update_execution_signals(self, execution_id: int, signals: dict) -> bool:
        """Update the signals_json for an existing execution."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE skill_executions SET signals_json = ? WHERE id = ?",
                (json.dumps(signals, ensure_ascii=False), execution_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def update_execution_score(self, execution_id: int, score: float, reasoning: str = "") -> bool:
        """Update the cross_model_score and reasoning for an execution."""
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE skill_executions
                   SET cross_model_score = ?, evaluator_reasoning = ?
                   WHERE id = ?""",
                (score, reasoning, execution_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_skill_executions_by_id(self, execution_id: int) -> dict | None:
        """Get a single execution by id, with parsed signals."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM skill_executions WHERE id = ?", (execution_id,)
            ).fetchone()
        return row_to_execution(row) if row else None

    def get_skill_executions(self, skill_name: str, limit: int = 50) -> list[dict]:
        """Get recent executions for a skill (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, skill_name, conversation_id, user_input, assistant_output,
                          signals_json, cross_model_score, evaluator_reasoning, created_at
                   FROM skill_executions
                   WHERE skill_name = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (skill_name, limit),
            ).fetchall()
        return [row_to_execution(r) for r in rows]

    # --- Emerged Skill API ---

    def register_emerged_skill(
        self,
        name: str,
        source_conversation_id: str | None = None,
        evaluation_criteria: str = "",
        status: str = "active",
        path: str = "",
        reason: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """Record metadata about a skill that emerged from a conversation."""
        normalised_status = self._normalise_emerged_skill_status(status)
        with self._lock:
            now = self._now()
            self._conn.execute(
                """INSERT INTO emerged_skills
                   (name, source_conversation_id, evaluation_criteria, path,
                    version, status, reason, evidence_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     evaluation_criteria = ?,
                     path = ?,
                     status = ?,
                     reason = ?,
                     evidence_json = ?,
                     updated_at = ?,
                     version = version + 1""",
                (
                    name,
                    source_conversation_id,
                    evaluation_criteria,
                    path,
                    normalised_status,
                    reason,
                    self._json_dump(evidence or {}),
                    now,
                    now,
                    evaluation_criteria,
                    path,
                    normalised_status,
                    reason,
                    self._json_dump(evidence or {}),
                    now,
                ),
            )
            self._conn.commit()

    def get_emerged_skill(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM emerged_skills WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["evidence"] = self._json_load_dict(payload.pop("evidence_json", ""))
        return payload

    def list_emerged_skills(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status:
                normalised_status = self._normalise_emerged_skill_status(status)
                rows = self._conn.execute(
                    "SELECT * FROM emerged_skills WHERE status = ? ORDER BY updated_at DESC",
                    (normalised_status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM emerged_skills ORDER BY updated_at DESC"
                ).fetchall()
        result = []
        for row in rows:
            payload = dict(row)
            payload["evidence"] = self._json_load_dict(payload.pop("evidence_json", ""))
            result.append(payload)
        return result

    def set_emerged_skill_status(
        self,
        name: str,
        status: str,
        reason: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        """Update lifecycle state for an emerged skill."""
        normalised_status = self._normalise_emerged_skill_status(status)
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE emerged_skills
                   SET status = ?, reason = ?, evidence_json = ?, updated_at = ?
                   WHERE name = ?""",
                (
                    normalised_status,
                    reason,
                    self._json_dump(evidence or {}),
                    self._now(),
                    name,
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def record_skill_lifecycle_event(
        self,
        skill_name: str,
        action: str,
        *,
        status: str = "",
        path: str = "",
        reason: str = "",
        evidence: dict[str, Any] | None = None,
        content_before: str = "",
        content_after: str = "",
    ) -> int:
        """Append an auditable lifecycle event for a skill."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO skill_lifecycle_events
                   (skill_name, action, status, path, reason, evidence_json,
                    content_before, content_after, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill_name,
                    action,
                    status,
                    path,
                    reason,
                    self._json_dump(evidence or {}),
                    content_before,
                    content_after,
                    self._now(),
                ),
            )
            self._conn.commit()
            return cursor.lastrowid or 0

    def list_skill_lifecycle_events(
        self, skill_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent lifecycle events, newest first."""
        with self._lock:
            if skill_name:
                rows = self._conn.execute(
                    """SELECT * FROM skill_lifecycle_events
                       WHERE skill_name = ?
                       ORDER BY id DESC
                       LIMIT ?""",
                    (skill_name, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM skill_lifecycle_events
                       ORDER BY id DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
        events = []
        for row in rows:
            payload = dict(row)
            payload["evidence"] = self._json_load_dict(payload.pop("evidence_json", ""))
            events.append(payload)
        return events

    def get_latest_skill_lifecycle_event(
        self, skill_name: str, action: str | None = None
    ) -> dict[str, Any] | None:
        """Return the newest lifecycle event for a skill, optionally filtered by action."""
        with self._lock:
            if action:
                row = self._conn.execute(
                    """SELECT * FROM skill_lifecycle_events
                       WHERE skill_name = ? AND action = ?
                       ORDER BY id DESC
                       LIMIT 1""",
                    (skill_name, action),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT * FROM skill_lifecycle_events
                       WHERE skill_name = ?
                       ORDER BY id DESC
                       LIMIT 1""",
                    (skill_name,),
                ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["evidence"] = self._json_load_dict(payload.pop("evidence_json", ""))
        return payload

    # --- Iteration Run API ---

    def record_iteration_run(
        self,
        *,
        domain: str,
        metrics_adapter: str,
        metrics_source: str = "",
        sort_metric: str = "",
        total_records: int = 0,
        summary: str,
        patterns: list[str] | None = None,
        raw_stats: dict[str, Any] | None = None,
        top_performers: list[dict[str, Any]] | None = None,
        bottom_performers: list[dict[str, Any]] | None = None,
        updates: list[dict[str, Any]] | None = None,
        commit_sha: str | None = None,
    ) -> int:
        """Persist one outcome iteration run and the files it touched."""
        if not domain.strip():
            raise ValueError("Iteration run domain must be non-empty")
        if not metrics_adapter.strip():
            raise ValueError("Iteration run metrics_adapter must be non-empty")
        if not summary.strip():
            raise ValueError("Iteration run summary must be non-empty")

        normalised_updates = self._normalise_iteration_updates(updates)
        with self._lock:
            now = self._now()
            cursor = self._conn.execute(
                """INSERT INTO iteration_runs
                   (domain, metrics_adapter, metrics_source, sort_metric, total_records,
                    summary, patterns_json, raw_stats_json, top_performers_json,
                    bottom_performers_json, commit_sha, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    domain.strip(),
                    metrics_adapter.strip(),
                    metrics_source.strip(),
                    sort_metric.strip(),
                    total_records,
                    summary.strip(),
                    self._json_dump(patterns or []),
                    self._json_dump(raw_stats or {}),
                    self._json_dump(top_performers or []),
                    self._json_dump(bottom_performers or []),
                    commit_sha,
                    now,
                ),
            )
            run_id = cursor.lastrowid or 0
            for update in normalised_updates:
                self._conn.execute(
                    """INSERT INTO iteration_run_updates
                       (run_id, path, strategy, has_changes)
                       VALUES (?, ?, ?, ?)""",
                    (
                        run_id,
                        update["path"],
                        update["strategy"],
                        int(update["has_changes"]),
                    ),
                )
            self._conn.commit()
            return run_id

    def list_iteration_runs(self, limit: int = 20) -> list[dict]:
        """List recent iteration runs with compact update counts."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT ir.*,
                          (SELECT COUNT(*) FROM iteration_run_updates WHERE run_id = ir.id)
                            AS update_count,
                          (SELECT COUNT(*) FROM iteration_run_updates
                           WHERE run_id = ir.id AND has_changes = 1) AS changed_count
                   FROM iteration_runs ir
                   ORDER BY ir.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        out: list[dict] = []
        for row in rows:
            item = row_to_iteration_run(row)
            item["update_count"] = row["update_count"]
            item["changed_count"] = row["changed_count"]
            out.append(item)
        return out

    def get_iteration_run(self, run_id: int) -> dict | None:
        """Fetch one iteration run with its file updates."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM iteration_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            updates = self._conn.execute(
                """SELECT path, strategy, has_changes
                   FROM iteration_run_updates
                   WHERE run_id = ?
                   ORDER BY id ASC""",
                (run_id,),
            ).fetchall()

        return row_to_iteration_run(
            row,
            updates=[
                {
                    "path": update["path"],
                    "strategy": update["strategy"],
                    "has_changes": bool(update["has_changes"]),
                }
                for update in updates
            ],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
