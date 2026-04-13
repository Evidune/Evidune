"""Tests for memory/store.py."""

from pathlib import Path

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
