"""Tests for memory/store.py."""

import sqlite3
from pathlib import Path
from threading import Thread

import pytest

from memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


class TestMessages:
    def test_add_and_get_history(self, store: MemoryStore):
        store.add_message("conv1", "user", "hello")
        store.add_message("conv1", "assistant", "hi there")
        history = store.get_history("conv1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_history_limit(self, store: MemoryStore):
        for i in range(10):
            store.add_message("conv1", "user", f"msg {i}")
        history = store.get_history("conv1", limit=3)
        assert len(history) == 3
        assert history[-1]["content"] == "msg 9"

    def test_separate_conversations(self, store: MemoryStore):
        store.add_message("conv1", "user", "hello from 1")
        store.add_message("conv2", "user", "hello from 2")
        h1 = store.get_history("conv1")
        h2 = store.get_history("conv2")
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0]["content"] == "hello from 1"

    def test_trim_history(self, store: MemoryStore):
        for i in range(20):
            store.add_message("conv1", "user", f"msg {i}")
        deleted = store.trim_history("conv1", keep=5)
        assert deleted == 15
        history = store.get_history("conv1", limit=100)
        assert len(history) == 5

    def test_trim_no_op_when_under_limit(self, store: MemoryStore):
        store.add_message("conv1", "user", "hello")
        deleted = store.trim_history("conv1", keep=10)
        assert deleted == 0


class TestFacts:
    def test_set_and_get_fact(self, store: MemoryStore):
        store.set_fact("user.name", "Alice")
        assert store.get_fact("user.name") == "Alice"

    def test_update_fact(self, store: MemoryStore):
        store.set_fact("key", "v1")
        store.set_fact("key", "v2")
        assert store.get_fact("key") == "v2"

    def test_get_missing_fact(self, store: MemoryStore):
        assert store.get_fact("nonexistent") is None

    def test_get_facts_with_prefix(self, store: MemoryStore):
        store.set_fact("user.name", "Alice")
        store.set_fact("user.role", "dev")
        store.set_fact("project.name", "aiflay")
        facts = store.get_facts(prefix="user.")
        assert len(facts) == 2
        keys = {f.key for f in facts}
        assert keys == {"user.name", "user.role"}

    def test_search_facts(self, store: MemoryStore):
        store.set_fact("pref1", "likes formal tone")
        store.set_fact("pref2", "prefers short answers")
        results = store.search_facts("formal")
        assert len(results) == 1
        assert results[0].key == "pref1"

    def test_delete_fact(self, store: MemoryStore):
        store.set_fact("key", "value")
        assert store.delete_fact("key") is True
        assert store.get_fact("key") is None
        assert store.delete_fact("key") is False


class TestSkillExecutions:
    def test_record_and_retrieve(self, store: MemoryStore):
        eid = store.record_execution(
            skill_name="write-article",
            user_input="write me an article",
            assistant_output="Here is the article...",
            conversation_id="conv-1",
        )
        assert eid > 0

        executions = store.get_skill_executions("write-article")
        assert len(executions) == 1
        assert executions[0]["skill_name"] == "write-article"
        assert executions[0]["user_input"] == "write me an article"
        assert executions[0]["score"] is None  # not yet evaluated

    def test_record_with_signals(self, store: MemoryStore):
        store.record_execution(
            skill_name="s",
            user_input="i",
            assistant_output="o",
            signals={"copied": True, "rating": 5},
        )
        executions = store.get_skill_executions("s")
        assert executions[0]["signals"] == {"copied": True, "rating": 5}

    def test_update_execution_score(self, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        ok = store.update_execution_score(eid, 0.85, "Good output")
        assert ok is True

        executions = store.get_skill_executions("s")
        assert executions[0]["score"] == 0.85
        assert executions[0]["evaluator_reasoning"] == "Good output"

    def test_update_execution_signals(self, store: MemoryStore):
        eid = store.record_execution(skill_name="s", user_input="i", assistant_output="o")
        ok = store.update_execution_signals(eid, {"thumbs_up": True})
        assert ok is True
        executions = store.get_skill_executions("s")
        assert executions[0]["signals"] == {"thumbs_up": True}

    def test_get_skill_executions_filters_by_name(self, store: MemoryStore):
        store.record_execution(skill_name="a", user_input="i", assistant_output="o")
        store.record_execution(skill_name="b", user_input="i", assistant_output="o")
        store.record_execution(skill_name="a", user_input="i2", assistant_output="o2")

        a_execs = store.get_skill_executions("a")
        b_execs = store.get_skill_executions("b")
        assert len(a_execs) == 2
        assert len(b_execs) == 1

    def test_get_skill_executions_newest_first(self, store: MemoryStore):
        store.record_execution(skill_name="s", user_input="first", assistant_output="o")
        store.record_execution(skill_name="s", user_input="second", assistant_output="o")
        execs = store.get_skill_executions("s")
        assert execs[0]["user_input"] == "second"
        assert execs[1]["user_input"] == "first"

    def test_executions_limit(self, store: MemoryStore):
        for i in range(20):
            store.record_execution(skill_name="s", user_input=f"in {i}", assistant_output="o")
        execs = store.get_skill_executions("s", limit=5)
        assert len(execs) == 5


class TestConversationManagement:
    def test_list_empty(self, store: MemoryStore):
        assert store.list_conversations() == []

    def test_list_after_add(self, store: MemoryStore):
        store.add_message("c1", "user", "hi")
        store.add_message("c2", "user", "hello")
        items = store.list_conversations()
        assert len(items) == 2
        ids = {i["id"] for i in items}
        assert ids == {"c1", "c2"}
        assert all("identity" in item for item in items)
        assert all(item["mode"] == "execute" for item in items)

    def test_list_includes_preview(self, store: MemoryStore):
        store.add_message("c1", "user", "first")
        store.add_message("c1", "assistant", "second reply")
        items = store.list_conversations()
        assert items[0]["preview"] == "second reply"
        assert items[0]["message_count"] == 2

    def test_list_orders_by_updated(self, store: MemoryStore):
        store.add_message("c1", "user", "old")
        store.add_message("c2", "user", "newer")
        items = store.list_conversations()
        assert items[0]["id"] == "c2"

    def test_list_filters_by_status(self, store: MemoryStore):
        store.add_message("c1", "user", "x")
        store.add_message("c2", "user", "y")
        store.set_conversation_status("c2", "archived")
        active = store.list_conversations()
        assert [i["id"] for i in active] == ["c1"]
        archived = store.list_conversations(status="archived")
        assert [i["id"] for i in archived] == ["c2"]
        all_ = store.list_conversations(status=None)
        assert len(all_) == 2

    def test_ensure_conversation_backfills_empty_channel(self, store: MemoryStore):
        store.add_message("c1", "user", "hi")
        assert store.get_conversation("c1")["channel"] == ""

        store.ensure_conversation("c1", channel="web")
        assert store.get_conversation("c1")["channel"] == "web"

    def test_ensure_conversation_does_not_overwrite_nonempty_channel(self, store: MemoryStore):
        store.ensure_conversation("c1", channel="cli")
        store.ensure_conversation("c1", channel="web")
        assert store.get_conversation("c1")["channel"] == "cli"

    def test_ensure_conversation_backfills_empty_identity(self, store: MemoryStore):
        store.ensure_conversation("c1", channel="web")
        assert store.get_conversation("c1")["identity"] == ""

        store.ensure_conversation("c1", identity="zhihu-writer")
        assert store.get_conversation("c1")["identity"] == "zhihu-writer"

    def test_set_title(self, store: MemoryStore):
        store.add_message("c1", "user", "x")
        assert store.set_conversation_title("c1", "My Chat") is True
        meta = store.get_conversation("c1")
        assert meta["title"] == "My Chat"

    def test_set_identity(self, store: MemoryStore):
        store.ensure_conversation("c1", channel="web")
        assert store.set_conversation_identity("c1", "zhihu-writer") is True
        assert store.get_conversation("c1")["identity"] == "zhihu-writer"

    def test_default_mode_is_execute(self, store: MemoryStore):
        store.ensure_conversation("c1")
        assert store.get_conversation("c1")["mode"] == "execute"
        assert store.get_conversation_mode("c1") == "execute"

    def test_set_mode(self, store: MemoryStore):
        store.ensure_conversation("c1", channel="web")
        assert store.set_conversation_mode("c1", "plan") is True
        assert store.get_conversation("c1")["mode"] == "plan"
        assert store.get_conversation_mode("c1") == "plan"

    def test_update_and_get_plan(self, store: MemoryStore):
        assert (
            store.update_conversation_plan(
                "c1",
                items=[
                    {"step": "Inspect the tool registry", "status": "completed"},
                    {"step": "Add plan tools", "status": "in_progress"},
                ],
                explanation="Ship plan support in small steps.",
            )
            is True
        )
        plan = store.get_conversation_plan("c1")
        assert plan == {
            "explanation": "Ship plan support in small steps.",
            "items": [
                {"step": "Inspect the tool registry", "status": "completed"},
                {"step": "Add plan tools", "status": "in_progress"},
            ],
        }
        meta = store.get_conversation("c1")
        assert meta["plan"] == plan

    def test_list_conversations_marks_has_plan(self, store: MemoryStore):
        store.update_conversation_plan(
            "c1",
            items=[{"step": "Inspect the system", "status": "pending"}],
        )
        items = store.list_conversations()
        assert items[0]["has_plan"] is True

    def test_clear_plan(self, store: MemoryStore):
        store.update_conversation_plan(
            "c1",
            items=[{"step": "Do the work", "status": "pending"}],
        )
        assert store.clear_conversation_plan("c1") is True
        assert store.get_conversation_plan("c1") is None

    def test_update_plan_rejects_multiple_in_progress_items(self, store: MemoryStore):
        with pytest.raises(ValueError, match="Only one plan item can be in_progress"):
            store.update_conversation_plan(
                "c1",
                items=[
                    {"step": "A", "status": "in_progress"},
                    {"step": "B", "status": "in_progress"},
                ],
            )

    def test_set_mode_rejects_unknown_value(self, store: MemoryStore):
        store.ensure_conversation("c1")
        with pytest.raises(ValueError, match="Invalid conversation mode"):
            store.set_conversation_mode("c1", "draft")

    def test_set_status_invalid_raises(self, store: MemoryStore):
        store.add_message("c1", "user", "x")
        with pytest.raises(ValueError):
            store.set_conversation_status("c1", "bogus")

    def test_delete_cascades(self, store: MemoryStore):
        store.add_message("c1", "user", "hi")
        store.record_execution(
            skill_name="s", user_input="i", assistant_output="o", conversation_id="c1"
        )
        assert store.delete_conversation("c1") is True
        assert store.get_conversation("c1") is None
        assert store.get_history("c1") == []
        assert store.get_skill_executions("s") == []

    def test_delete_missing_returns_false(self, store: MemoryStore):
        assert store.delete_conversation("nonexistent") is False

    def test_cross_thread_reads_and_writes_use_same_store(self, store: MemoryStore):
        store.add_message("c1", "user", "hi")

        result: dict[str, object] = {}

        def worker() -> None:
            result["list"] = store.list_conversations(status=None)
            result["archived"] = store.set_conversation_status("c1", "archived")
            result["meta"] = store.get_conversation("c1")

        thread = Thread(target=worker)
        thread.start()
        thread.join()

        assert result["archived"] is True
        assert result["list"]
        assert result["meta"]["status"] == "archived"


class TestEmergedSkills:
    def test_register_and_get(self, store: MemoryStore):
        store.register_emerged_skill(
            name="my-emerged",
            source_conversation_id="conv-1",
            evaluation_criteria="user accepts output without modification",
            path="/tmp/my-emerged/SKILL.md",
            reason="Auto activated",
            evidence={"pattern_confidence": 0.9},
        )
        skill = store.get_emerged_skill("my-emerged")
        assert skill is not None
        assert skill["name"] == "my-emerged"
        assert skill["source_conversation_id"] == "conv-1"
        assert skill["status"] == "active"
        assert skill["path"] == "/tmp/my-emerged/SKILL.md"
        assert skill["reason"] == "Auto activated"
        assert skill["evidence"] == {"pattern_confidence": 0.9}
        assert skill["version"] == 1

    def test_register_twice_increments_version(self, store: MemoryStore):
        store.register_emerged_skill(name="x", evaluation_criteria="v1")
        store.register_emerged_skill(name="x", evaluation_criteria="v2")
        skill = store.get_emerged_skill("x")
        assert skill["version"] == 2
        assert skill["evaluation_criteria"] == "v2"

    def test_get_missing_skill(self, store: MemoryStore):
        assert store.get_emerged_skill("nonexistent") is None

    def test_list_all_emerged(self, store: MemoryStore):
        store.register_emerged_skill(name="a", status="active")
        store.register_emerged_skill(name="b", status="pending_review")
        skills = store.list_emerged_skills()
        assert len(skills) == 2

    def test_list_filtered_by_status(self, store: MemoryStore):
        store.register_emerged_skill(name="a", status="active")
        store.register_emerged_skill(name="b", status="pending_review")
        active = store.list_emerged_skills(status="active")
        assert len(active) == 1
        assert active[0]["name"] == "a"

    def test_set_status_updates_reason_and_evidence(self, store: MemoryStore):
        store.register_emerged_skill(name="a", status="active")
        ok = store.set_emerged_skill_status(
            "a",
            "rolled_back",
            reason="Negative feedback",
            evidence={"combined_confidence": -1.0},
        )
        assert ok is True
        skill = store.get_emerged_skill("a")
        assert skill["status"] == "rolled_back"
        assert skill["reason"] == "Negative feedback"
        assert skill["evidence"] == {"combined_confidence": -1.0}
        state = store.get_skill_state("a")
        assert state is not None
        assert state["status"] == "rolled_back"
        assert state["origin"] == "emerged"

    def test_record_and_list_lifecycle_events(self, store: MemoryStore):
        event_id = store.record_skill_lifecycle_event(
            "a",
            "activate",
            status="active",
            path="/tmp/a/SKILL.md",
            harness_task_id="task-1",
            reason="Auto activated",
            evidence={"pattern_confidence": 0.8},
            content_after="skill body",
        )
        assert event_id > 0
        events = store.list_skill_lifecycle_events("a")
        assert len(events) == 1
        assert events[0]["action"] == "activate"
        assert events[0]["status"] == "active"
        assert events[0]["harness_task_id"] == "task-1"
        assert events[0]["evidence"] == {"pattern_confidence": 0.8}
        latest = store.get_latest_skill_lifecycle_event("a", action="activate")
        assert latest is not None
        assert latest["id"] == event_id


class TestSkillStates:
    def test_upsert_and_get_base_skill_state(self, store: MemoryStore):
        store.upsert_skill_state(
            "writer",
            origin="base",
            path="/tmp/writer/SKILL.md",
            status="disabled",
            reason="Operator hold",
            evidence={"signal": "thumbs_down"},
        )
        state = store.get_skill_state("writer")
        assert state is not None
        assert state["skill_name"] == "writer"
        assert state["origin"] == "base"
        assert state["status"] == "disabled"
        assert state["path"] == "/tmp/writer/SKILL.md"
        assert state["reason"] == "Operator hold"
        assert state["evidence"] == {"signal": "thumbs_down"}

    def test_set_skill_state_updates_existing_row(self, store: MemoryStore):
        store.upsert_skill_state("writer", origin="base", path="/tmp/writer/SKILL.md")
        assert (
            store.set_skill_state(
                "writer",
                "rolled_back",
                reason="Negative evidence",
                evidence={"combined_confidence": -0.8},
            )
            is True
        )
        state = store.get_skill_state("writer")
        assert state["status"] == "rolled_back"
        assert state["reason"] == "Negative evidence"
        assert state["evidence"] == {"combined_confidence": -0.8}

    def test_resolve_skill_status_defaults_to_active(self, store: MemoryStore):
        assert store.resolve_skill_status("missing") == "active"

    def test_register_emerged_skill_mirrors_skill_state(self, store: MemoryStore):
        store.register_emerged_skill(
            name="emerged", status="pending_review", path="/tmp/e/SKILL.md"
        )
        state = store.get_skill_state("emerged")
        assert state is not None
        assert state["origin"] == "emerged"
        assert state["status"] == "pending_review"

    def test_list_skill_states_filters_by_status(self, store: MemoryStore):
        store.upsert_skill_state("a", origin="base", status="active")
        store.upsert_skill_state("b", origin="emerged", status="disabled")
        disabled = store.list_skill_states(status="disabled")
        assert len(disabled) == 1
        assert disabled[0]["skill_name"] == "b"


class TestIterationRuns:
    def test_record_and_get_iteration_run(self, store: MemoryStore):
        run_id = store.record_iteration_run(
            domain="zhihu",
            metrics_adapter="generic_csv",
            metrics_source="data/zhihu.csv",
            sort_metric="reads",
            total_records=3,
            summary="zhihu: 3 items, total reads=1234, avg=411.",
            patterns=["Top performers have longer titles"],
            raw_stats={"reads": {"total": 1234, "avg": 411.3}},
            top_performers=[{"title": "A", "metrics": {"reads": 900}, "metadata": {}}],
            bottom_performers=[{"title": "B", "metrics": {"reads": 10}, "metadata": {}}],
            updates=[
                {
                    "path": "skills/write-article/SKILL.md",
                    "strategy": "replace_section",
                    "has_changes": True,
                },
                {
                    "path": "refs/case-studies.md",
                    "strategy": "full_replace",
                    "has_changes": False,
                },
            ],
        )

        run = store.get_iteration_run(run_id)
        assert run is not None
        assert run["domain"] == "zhihu"
        assert run["metrics_adapter"] == "generic_csv"
        assert run["patterns"] == ["Top performers have longer titles"]
        assert run["updates"] == [
            {
                "path": "skills/write-article/SKILL.md",
                "strategy": "replace_section",
                "has_changes": True,
            },
            {
                "path": "refs/case-studies.md",
                "strategy": "full_replace",
                "has_changes": False,
            },
        ]

    def test_list_iteration_runs_includes_update_counts(self, store: MemoryStore):
        first_id = store.record_iteration_run(
            domain="zhihu",
            metrics_adapter="generic_csv",
            summary="first",
            updates=[
                {"path": "a.md", "strategy": "append_only", "has_changes": True},
                {"path": "b.md", "strategy": "append_only", "has_changes": False},
            ],
        )
        second_id = store.record_iteration_run(
            domain="zhihu",
            metrics_adapter="generic_csv",
            summary="second",
            updates=[{"path": "c.md", "strategy": "append_only", "has_changes": True}],
        )

        runs = store.list_iteration_runs()
        assert [run["id"] for run in runs[:2]] == [second_id, first_id]
        assert runs[0]["changed_count"] == 1
        assert runs[0]["update_count"] == 1
        assert runs[1]["changed_count"] == 1
        assert runs[1]["update_count"] == 2

    def test_record_iteration_run_rejects_invalid_update(self, store: MemoryStore):
        with pytest.raises(ValueError, match="non-empty path"):
            store.record_iteration_run(
                domain="zhihu",
                metrics_adapter="generic_csv",
                summary="x",
                updates=[{"path": "", "strategy": "append_only"}],
            )

    def test_migration_keeps_old_db_and_adds_iteration_tables(self, tmp_path: Path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                channel TEXT DEFAULT '',
                persona TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE facts (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                source TEXT DEFAULT 'agent',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

        store = MemoryStore(db_path)
        try:
            tables = {
                row["name"]
                for row in store._conn.execute(  # noqa: SLF001 - migration assertion
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            store.close()

        assert "iteration_runs" in tables
        assert "iteration_run_updates" in tables
