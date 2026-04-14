"""SQLite-backed persistent memory store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    conversation_id: str
    created_at: str = ""


@dataclass
class Fact:
    key: str
    value: str
    source: str = "agent"
    created_at: str = ""
    updated_at: str = ""


class MemoryStore:
    """SQLite-backed cross-session memory.

    Stores conversation history and persistent facts.
    """

    def __init__(self, db_path: str | Path = "~/.aiflay/memory.db") -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                channel TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                conversation_id TEXT,
                user_input TEXT NOT NULL,
                assistant_output TEXT NOT NULL,
                signals_json TEXT DEFAULT '{}',
                cross_model_score REAL,
                evaluator_reasoning TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_executions_skill
                ON skill_executions(skill_name);

            CREATE TABLE IF NOT EXISTS emerged_skills (
                name TEXT PRIMARY KEY,
                source_conversation_id TEXT,
                evaluation_criteria TEXT,
                version INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending_review',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                source TEXT DEFAULT 'agent',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source);
        """
        )
        self._conn.commit()

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
        """Get recent messages for a conversation."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        # Return in chronological order
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

    # --- Facts API ---

    def set_fact(self, key: str, value: str, source: str = "agent") -> None:
        """Set or update a persistent fact."""
        now = self._now()
        self._conn.execute(
            """INSERT INTO facts (key, value, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, source = ?, updated_at = ?""",
            (key, value, source, now, now, value, source, now),
        )
        self._conn.commit()

    def get_fact(self, key: str) -> str | None:
        """Get a single fact by key."""
        row = self._conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_facts(self, prefix: str | None = None) -> list[Fact]:
        """Get all facts, optionally filtered by key prefix."""
        if prefix:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE key LIKE ? ORDER BY key",
                (f"{prefix}%",),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM facts ORDER BY key").fetchall()
        return [
            Fact(
                key=r["key"],
                value=r["value"],
                source=r["source"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def search_facts(self, query: str) -> list[Fact]:
        """Search facts by value content (simple LIKE search)."""
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE value LIKE ? OR key LIKE ? ORDER BY updated_at DESC",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return [
            Fact(
                key=r["key"],
                value=r["value"],
                source=r["source"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def delete_fact(self, key: str) -> bool:
        """Delete a fact. Returns True if it existed."""
        cursor = self._conn.execute("DELETE FROM facts WHERE key = ?", (key,))
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
        import json as _json

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
                _json.dumps(signals or {}, ensure_ascii=False),
                cross_model_score,
                evaluator_reasoning,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def update_execution_signals(self, execution_id: int, signals: dict) -> bool:
        """Update the signals_json for an existing execution."""
        import json as _json

        cursor = self._conn.execute(
            "UPDATE skill_executions SET signals_json = ? WHERE id = ?",
            (_json.dumps(signals, ensure_ascii=False), execution_id),
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

    def get_skill_executions(self, skill_name: str, limit: int = 50) -> list[dict]:
        """Get recent executions for a skill (newest first)."""
        import json as _json

        rows = self._conn.execute(
            """SELECT id, skill_name, conversation_id, user_input, assistant_output,
                      signals_json, cross_model_score, evaluator_reasoning, created_at
               FROM skill_executions
               WHERE skill_name = ?
               ORDER BY id DESC
               LIMIT ?""",
            (skill_name, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "skill_name": r["skill_name"],
                "conversation_id": r["conversation_id"],
                "user_input": r["user_input"],
                "assistant_output": r["assistant_output"],
                "signals": _json.loads(r["signals_json"] or "{}"),
                "score": r["cross_model_score"],
                "evaluator_reasoning": r["evaluator_reasoning"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

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
