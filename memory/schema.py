"""SQLite DDL and migrations for the aiflay memory store.

Kept separate from `store.py` so the table definitions are easy to
audit and extend without scrolling past the entire API surface.
"""

from __future__ import annotations

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    channel TEXT DEFAULT '',
    identity TEXT DEFAULT '',
    plan_json TEXT DEFAULT '',
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);

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

CREATE INDEX IF NOT EXISTS idx_executions_skill ON skill_executions(skill_name);

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
    namespace TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'agent',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source);
CREATE INDEX IF NOT EXISTS idx_facts_namespace ON facts(namespace);
"""


_MIGRATE_FACTS_NAMESPACE = """
ALTER TABLE facts RENAME TO facts_old;
CREATE TABLE facts (
    namespace TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'agent',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
INSERT INTO facts (namespace, key, value, source, created_at, updated_at)
    SELECT '', key, value, source, created_at, updated_at FROM facts_old;
DROP TABLE facts_old;
CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source);
CREATE INDEX IF NOT EXISTS idx_facts_namespace ON facts(namespace);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing and migrate old schemas if needed."""
    conn.executescript(_SCHEMA)
    _migrate_facts_namespace(conn)
    _migrate_conversations_metadata(conn)
    conn.commit()


def _migrate_facts_namespace(conn: sqlite3.Connection) -> None:
    """Older DBs have facts(key PRIMARY KEY) without namespace; migrate."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()]
    if "namespace" in cols:
        return
    conn.executescript(_MIGRATE_FACTS_NAMESPACE)


def _migrate_conversations_metadata(conn: sqlite3.Connection) -> None:
    """Older DBs have conversations without newer metadata columns; ADD them."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "identity" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN identity TEXT DEFAULT ''")
    if "persona" in cols:
        conn.execute(
            "UPDATE conversations SET identity = persona WHERE identity = '' AND persona != ''"
        )
    if "plan_json" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN plan_json TEXT DEFAULT ''")
    if "title" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT DEFAULT ''")
    if "status" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN status TEXT DEFAULT 'active'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status)")
