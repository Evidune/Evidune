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

from memory.rows import row_to_execution, row_to_fact
from memory.schema import init_schema
from memory.store_models import Fact, Message  # noqa: F401 — re-exported

__all__ = ["MemoryStore", "Fact", "Message"]


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
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        init_schema(self._conn)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Conversation / Message API ---

    def ensure_conversation(self, conversation_id: str, channel: str = "") -> None:
        """Create a conversation if it doesn't exist."""
        now = self._now()
        self._conn.execute(
            "INSERT OR IGNORE INTO conversations (id, channel, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, channel, now, now),
        )
        self._conn.commit()

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        """Store a message in conversation history."""
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
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def trim_history(self, conversation_id: str, keep: int = 100) -> int:
        """Delete old messages beyond the keep limit. Returns number deleted."""
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
        rows = self._conn.execute(
            f"""SELECT c.id, c.channel, c.title, c.status,
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
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_conversation_title(self, conversation_id: str, title: str) -> bool:
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
        cursor = self._conn.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), conversation_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and all its messages + skill executions.

        Returns True if the conversation row existed.
        """
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self._conn.execute(
            "DELETE FROM skill_executions WHERE conversation_id = ?", (conversation_id,)
        )
        cursor = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    # --- Facts API (namespaced) ---
    #
    # namespace="" is the global / shared namespace (default).
    # namespace="persona:<name>" isolates one assistant identity's facts.
    # All read/write helpers default to the global namespace for
    # backward compatibility.

    def set_fact(self, key: str, value: str, source: str = "agent", namespace: str = "") -> None:
        """Set or update a persistent fact in the given namespace."""
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
        row = self._conn.execute(
            "SELECT value FROM facts WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        return row["value"] if row else None

    def get_facts(self, prefix: str | None = None, namespace: str | None = "") -> list[Fact]:
        """Get facts in a namespace, optionally filtered by key prefix.

        Pass namespace=None to get facts across ALL namespaces.
        """
        if namespace is None:
            if prefix:
                rows = self._conn.execute(
                    "SELECT * FROM facts WHERE key LIKE ? ORDER BY namespace, key",
                    (f"{prefix}%",),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM facts ORDER BY namespace, key").fetchall()
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
        cursor = self._conn.execute(
            "UPDATE skill_executions SET signals_json = ? WHERE id = ?",
            (json.dumps(signals, ensure_ascii=False), execution_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def update_execution_score(self, execution_id: int, score: float, reasoning: str = "") -> bool:
        """Update the cross_model_score and reasoning for an execution."""
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
        row = self._conn.execute(
            "SELECT * FROM skill_executions WHERE id = ?", (execution_id,)
        ).fetchone()
        return row_to_execution(row) if row else None

    def get_skill_executions(self, skill_name: str, limit: int = 50) -> list[dict]:
        """Get recent executions for a skill (newest first)."""
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
        status: str = "pending_review",
    ) -> None:
        """Record metadata about a skill that emerged from a conversation."""
        now = self._now()
        self._conn.execute(
            """INSERT INTO emerged_skills
               (name, source_conversation_id, evaluation_criteria,
                version, status, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 evaluation_criteria = ?,
                 status = ?,
                 updated_at = ?,
                 version = version + 1""",
            (
                name,
                source_conversation_id,
                evaluation_criteria,
                status,
                now,
                now,
                evaluation_criteria,
                status,
                now,
            ),
        )
        self._conn.commit()

    def get_emerged_skill(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM emerged_skills WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_emerged_skills(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM emerged_skills WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM emerged_skills ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
