"""SQLite DDL and migrations for the evidune memory store.

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
    turn_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_uid TEXT DEFAULT '',
    skill_name TEXT NOT NULL,
    skill_version TEXT DEFAULT '',
    skill_digest TEXT DEFAULT '',
    conversation_id TEXT,
    harness_task_id TEXT DEFAULT '',
    experiment_id TEXT DEFAULT '',
    corpus_id TEXT DEFAULT '',
    benchmark_task_id TEXT DEFAULT '',
    variant TEXT DEFAULT '',
    user_input TEXT NOT NULL,
    assistant_output TEXT NOT NULL,
    tool_trace_json TEXT DEFAULT '[]',
    artifact_refs_json TEXT DEFAULT '[]',
    external_entities_json TEXT DEFAULT '[]',
    model_ref_json TEXT DEFAULT '{}',
    execution_contract_digest TEXT DEFAULT '',
    outcome_contract_digest TEXT DEFAULT '',
    signals_json TEXT DEFAULT '{}',
    cross_model_score REAL,
    evaluator_reasoning TEXT,
    started_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS skill_evaluation_contracts (
    skill_name TEXT PRIMARY KEY,
    contract_json TEXT NOT NULL,
    source TEXT DEFAULT 'runtime',
    path TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    aggregate_score REAL NOT NULL,
    criteria_scores_json TEXT DEFAULT '{}',
    observed_metrics_json TEXT DEFAULT '{}',
    missing_observations_json TEXT DEFAULT '[]',
    reasoning TEXT DEFAULT '',
    contract_version INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES skill_executions(id)
);

CREATE TABLE IF NOT EXISTS evaluation_contract_snapshots (
    digest TEXT PRIMARY KEY,
    contract_kind TEXT NOT NULL,
    contract_version TEXT DEFAULT '',
    contract_json TEXT NOT NULL,
    source TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_uid TEXT NOT NULL UNIQUE,
    execution_id INTEGER NOT NULL,
    skill_name TEXT DEFAULT '',
    skill_version TEXT DEFAULT '',
    evaluator_id TEXT NOT NULL,
    evaluator_revision TEXT NOT NULL,
    evaluator_type TEXT NOT NULL,
    contract_digest TEXT DEFAULT '',
    verdict TEXT NOT NULL,
    score REAL,
    uncertainty TEXT DEFAULT 'unknown',
    dimensions_json TEXT DEFAULT '{}',
    failure_modes_json TEXT DEFAULT '[]',
    evidence_refs_json TEXT DEFAULT '[]',
    hard_gate_failures_json TEXT DEFAULT '[]',
    attribution_grade TEXT DEFAULT 'unknown',
    reasoning TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES skill_executions(id)
);

CREATE TABLE IF NOT EXISTS evidence_bindings (
    id TEXT PRIMARY KEY,
    execution_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    skill_version TEXT DEFAULT '',
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    intervention_json TEXT DEFAULT '{}',
    expected_state_json TEXT DEFAULT '{}',
    forbidden_state_json TEXT DEFAULT '{}',
    observation_plan_json TEXT DEFAULT '{}',
    attribution_policy TEXT DEFAULT 'unknown',
    minimum_evidence_grade TEXT DEFAULT 'unknown',
    probe_digest TEXT DEFAULT '',
    evaluator_digest TEXT DEFAULT '',
    contract_digest TEXT DEFAULT '',
    status TEXT DEFAULT 'committed',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES skill_executions(id)
);

CREATE TABLE IF NOT EXISTS probe_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id TEXT NOT NULL,
    horizon_id TEXT NOT NULL,
    probe_revision TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    lease_owner TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(binding_id, horizon_id, probe_revision, attempt_number),
    FOREIGN KEY (binding_id) REFERENCES evidence_bindings(id)
);

CREATE TABLE IF NOT EXISTS evidence_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id TEXT NOT NULL,
    horizon_id TEXT NOT NULL,
    probe_revision TEXT NOT NULL,
    observation_kind TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    evidence_ref TEXT DEFAULT '',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(binding_id, horizon_id, probe_revision),
    FOREIGN KEY (binding_id) REFERENCES evidence_bindings(id)
);

CREATE TABLE IF NOT EXISTS evidence_horizon_leases (
    binding_id TEXT NOT NULL,
    horizon_id TEXT NOT NULL,
    probe_revision TEXT NOT NULL,
    owner TEXT NOT NULL,
    leased_until TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (binding_id, horizon_id, probe_revision),
    FOREIGN KEY (binding_id) REFERENCES evidence_bindings(id)
);

CREATE TABLE IF NOT EXISTS skill_version_experiments (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    parent_version TEXT DEFAULT '',
    parent_digest TEXT NOT NULL,
    parent_content TEXT NOT NULL,
    candidate_version TEXT DEFAULT '',
    candidate_digest TEXT NOT NULL,
    candidate_content TEXT NOT NULL,
    source_execution_ids_json TEXT DEFAULT '[]',
    corpus_id TEXT DEFAULT '',
    split TEXT DEFAULT '',
    model_ref_json TEXT DEFAULT '{}',
    budget_json TEXT DEFAULT '{}',
    policy_json TEXT DEFAULT '{}',
    evidence_json TEXT DEFAULT '{}',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_experiment_trials (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    split TEXT NOT NULL,
    variant TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    execution_id INTEGER,
    status TEXT NOT NULL,
    classification TEXT DEFAULT '',
    result_json TEXT DEFAULT '{}',
    started_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id, task_ref, variant, trial_number),
    FOREIGN KEY (experiment_id) REFERENCES skill_version_experiments(id),
    FOREIGN KEY (execution_id) REFERENCES skill_executions(id)
);

CREATE TABLE IF NOT EXISTS outcome_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    observed_at TEXT DEFAULT '',
    metrics_json TEXT DEFAULT '{}',
    dimensions_json TEXT DEFAULT '{}',
    source TEXT DEFAULT '',
    skill_version TEXT DEFAULT '',
    run_id INTEGER DEFAULT 0,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcome_window_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    primary_kpi TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    baseline_value REAL,
    current_value REAL,
    delta REAL,
    confidence REAL NOT NULL DEFAULT 0,
    window_json TEXT DEFAULT '{}',
    segment_breakdown_json TEXT DEFAULT '[]',
    policy_state_json TEXT DEFAULT '{}',
    raw_stats_json TEXT DEFAULT '{}',
    exemplar_slice_json TEXT DEFAULT '[]',
    run_id INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS graph_memory_nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    key TEXT NOT NULL,
    text TEXT DEFAULT '',
    source_type TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(node_type, key, source_type, source_id)
);

CREATE TABLE IF NOT EXISTS graph_memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(from_node_id, to_node_id, edge_type),
    FOREIGN KEY (from_node_id) REFERENCES graph_memory_nodes(node_id),
    FOREIGN KEY (to_node_id) REFERENCES graph_memory_nodes(node_id)
);

CREATE TABLE IF NOT EXISTS graph_memory_traces (
    trace_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    seed_nodes_json TEXT DEFAULT '[]',
    selected_nodes_json TEXT DEFAULT '[]',
    selected_skills_json TEXT DEFAULT '[]',
    actions_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
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
    if "turn_count" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN turn_count INTEGER DEFAULT 0")
        conn.execute(
            """UPDATE conversations
               SET turn_count = (
                   SELECT COUNT(*)
                   FROM messages
                   WHERE messages.conversation_id = conversations.id
                     AND messages.role = 'user'
               )"""
        )


def _migrate_skill_executions(conn: sqlite3.Connection) -> None:
    """Add immutable lineage metadata to older execution tables."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(skill_executions)").fetchall()]
    additions = {
        "execution_uid": "TEXT DEFAULT ''",
        "skill_version": "TEXT DEFAULT ''",
        "skill_digest": "TEXT DEFAULT ''",
        "harness_task_id": "TEXT DEFAULT ''",
        "experiment_id": "TEXT DEFAULT ''",
        "corpus_id": "TEXT DEFAULT ''",
        "benchmark_task_id": "TEXT DEFAULT ''",
        "variant": "TEXT DEFAULT ''",
        "tool_trace_json": "TEXT DEFAULT '[]'",
        "artifact_refs_json": "TEXT DEFAULT '[]'",
        "external_entities_json": "TEXT DEFAULT '[]'",
        "model_ref_json": "TEXT DEFAULT '{}'",
        "execution_contract_digest": "TEXT DEFAULT ''",
        "outcome_contract_digest": "TEXT DEFAULT ''",
        "started_at": "TEXT DEFAULT ''",
        "completed_at": "TEXT DEFAULT ''",
    }
    for column, declaration in additions.items():
        if column not in cols:
            conn.execute(f"ALTER TABLE skill_executions ADD COLUMN {column} {declaration}")


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_executions_uid ON skill_executions(execution_uid)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_version "
        "ON skill_executions(skill_name, skill_version)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_experiment " "ON skill_executions(experiment_id)"
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
        "CREATE INDEX IF NOT EXISTS idx_skill_evaluations_skill ON skill_evaluations(skill_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_evaluations_execution ON skill_evaluations(execution_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evaluation_results_execution "
        "ON evaluation_results(execution_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evaluation_results_skill_version "
        "ON evaluation_results(skill_name, skill_version)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_bindings_execution "
        "ON evidence_bindings(execution_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_bindings_status ON evidence_bindings(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_probe_attempts_binding ON probe_attempts(binding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_observations_binding "
        "ON evidence_observations(binding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_leases_until "
        "ON evidence_horizon_leases(leased_until)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_experiments_skill "
        "ON skill_version_experiments(skill_name, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_experiment_trials_experiment "
        "ON skill_experiment_trials(experiment_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_observations_skill ON outcome_observations(skill_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_observations_run ON outcome_observations(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_summaries_skill ON outcome_window_summaries(skill_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outcome_summaries_run ON outcome_window_summaries(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_tasks_conv ON harness_tasks(conversation_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_harness_steps_task ON harness_steps(task_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_harness_artifacts_task ON harness_artifacts(task_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_memory_nodes(node_type)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_graph_nodes_source ON graph_memory_nodes(source_type, source_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_key ON graph_memory_nodes(key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_graph_edges_from ON graph_memory_edges(from_node_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_to ON graph_memory_edges(to_node_id)")
