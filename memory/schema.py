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
    squad_profile TEXT DEFAULT '',
    mode TEXT DEFAULT 'execute',
    plan_json TEXT DEFAULT '',
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    conversation_id TEXT,
    harness_task_id TEXT DEFAULT '',
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
    path TEXT DEFAULT '',
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    reason TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_states (
    skill_name TEXT PRIMARY KEY,
    origin TEXT NOT NULL DEFAULT 'base',
    path TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    reason TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT DEFAULT '',
    path TEXT DEFAULT '',
    harness_task_id TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    content_before TEXT DEFAULT '',
    content_after TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS squad_profiles (
    name TEXT PRIMARY KEY,
    roles_json TEXT DEFAULT '[]',
    config_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_tasks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT DEFAULT '',
    surface TEXT DEFAULT 'serve',
    squad_profile TEXT DEFAULT '',
    status TEXT DEFAULT 'running',
    task_kind TEXT DEFAULT 'conversation',
    user_input TEXT DEFAULT '',
    selected_skills_json TEXT DEFAULT '[]',
    role_roster_json TEXT DEFAULT '[]',
    budget_json TEXT DEFAULT '{}',
    environment_id TEXT DEFAULT '',
    environment_status TEXT DEFAULT '',
    artifact_manifest_json TEXT DEFAULT '{}',
    validation_summary_json TEXT DEFAULT '{}',
    delivery_summary_json TEXT DEFAULT '{}',
    escalation_reason TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    convergence_json TEXT DEFAULT '{}',
    final_output TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT DEFAULT 'completed',
    summary TEXT DEFAULT '',
    tool_trace_json TEXT DEFAULT '[]',
    budget_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES harness_tasks(id)
);

CREATE TABLE IF NOT EXISTS harness_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step_id INTEGER DEFAULT 0,
    phase TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT DEFAULT 'note',
    summary TEXT DEFAULT '',
    content TEXT DEFAULT '',
    accepted INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES harness_tasks(id)
);

CREATE TABLE IF NOT EXISTS iteration_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    metrics_adapter TEXT NOT NULL,
    metrics_source TEXT DEFAULT '',
    sort_metric TEXT DEFAULT '',
    total_records INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    patterns_json TEXT DEFAULT '[]',
    raw_stats_json TEXT DEFAULT '{}',
    top_performers_json TEXT DEFAULT '[]',
    bottom_performers_json TEXT DEFAULT '[]',
    commit_sha TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iteration_run_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    strategy TEXT NOT NULL,
    has_changes INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES iteration_runs(id)
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
    _migrate_skill_executions(conn)
    _migrate_emerged_skills(conn)
    _migrate_skill_states(conn)
    _migrate_skill_lifecycle_events(conn)
    _migrate_harness_tasks(conn)
    _ensure_indexes(conn)
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
    if "squad_profile" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN squad_profile TEXT DEFAULT ''")
    if "persona" in cols:
        conn.execute(
            "UPDATE conversations SET identity = persona WHERE identity = '' AND persona != ''"
        )
    if "plan_json" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN plan_json TEXT DEFAULT ''")
    if "mode" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN mode TEXT DEFAULT 'execute'")
    if "title" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT DEFAULT ''")
    if "status" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN status TEXT DEFAULT 'active'")


def _migrate_skill_executions(conn: sqlite3.Connection) -> None:
    """Older DBs do not track harness_task_id on executions."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(skill_executions)").fetchall()]
    if "harness_task_id" not in cols:
        conn.execute("ALTER TABLE skill_executions ADD COLUMN harness_task_id TEXT DEFAULT ''")


def _migrate_emerged_skills(conn: sqlite3.Connection) -> None:
    """Older DBs have thinner emerged_skill rows; add lifecycle metadata columns."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(emerged_skills)").fetchall()]
    if not cols:
        return
    if "path" not in cols:
        conn.execute("ALTER TABLE emerged_skills ADD COLUMN path TEXT DEFAULT ''")
    if "reason" not in cols:
        conn.execute("ALTER TABLE emerged_skills ADD COLUMN reason TEXT DEFAULT ''")
    if "evidence_json" not in cols:
        conn.execute("ALTER TABLE emerged_skills ADD COLUMN evidence_json TEXT DEFAULT '{}'")


def _migrate_skill_states(conn: sqlite3.Connection) -> None:
    """Seed unified skill-state rows from emerged-skill metadata."""
    conn.execute(
        """INSERT OR IGNORE INTO skill_states
           (skill_name, origin, path, status, reason, evidence_json, created_at, updated_at)
           SELECT name, 'emerged', path, status, reason, evidence_json, created_at, updated_at
           FROM emerged_skills"""
    )


def _migrate_skill_lifecycle_events(conn: sqlite3.Connection) -> None:
    """Older DBs do not track harness_task_id on lifecycle events."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(skill_lifecycle_events)").fetchall()]
    if "harness_task_id" not in cols:
        conn.execute(
            "ALTER TABLE skill_lifecycle_events ADD COLUMN harness_task_id TEXT DEFAULT ''"
        )


def _migrate_harness_tasks(conn: sqlite3.Connection) -> None:
    """Older DBs lack runtime, validation, and delivery metadata on harness tasks."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(harness_tasks)").fetchall()]
    if not cols:
        return
    if "environment_id" not in cols:
        conn.execute("ALTER TABLE harness_tasks ADD COLUMN environment_id TEXT DEFAULT ''")
    if "environment_status" not in cols:
        conn.execute("ALTER TABLE harness_tasks ADD COLUMN environment_status TEXT DEFAULT ''")
    if "artifact_manifest_json" not in cols:
        conn.execute(
            "ALTER TABLE harness_tasks ADD COLUMN artifact_manifest_json TEXT DEFAULT '{}'"
        )
    if "validation_summary_json" not in cols:
        conn.execute(
            "ALTER TABLE harness_tasks ADD COLUMN validation_summary_json TEXT DEFAULT '{}'"
        )
    if "delivery_summary_json" not in cols:
        conn.execute("ALTER TABLE harness_tasks ADD COLUMN delivery_summary_json TEXT DEFAULT '{}'")
    if "escalation_reason" not in cols:
        conn.execute("ALTER TABLE harness_tasks ADD COLUMN escalation_reason TEXT DEFAULT ''")


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes only after legacy-column migrations have completed."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_squad ON conversations(squad_profile)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_task ON skill_executions(harness_task_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_namespace ON facts(namespace)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iteration_runs_created ON iteration_runs(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_iteration_updates_run ON iteration_run_updates(run_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_states_status ON skill_states(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_states_origin ON skill_states(origin)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_skill ON skill_lifecycle_events(skill_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_task ON skill_lifecycle_events(harness_task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_tasks_conv ON harness_tasks(conversation_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_harness_steps_task ON harness_steps(task_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_artifacts_task ON harness_artifacts(task_id)"
    )
