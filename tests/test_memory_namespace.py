"""Tests for namespaced facts in memory/store.py."""

from pathlib import Path

import pytest

from memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


class TestNamespacedFacts:
    def test_default_namespace_is_global(self, store: MemoryStore):
        store.set_fact("k", "v")
        assert store.get_fact("k") == "v"
        # Same key in a different namespace should not collide
        store.set_fact("k", "v2", namespace="persona:alice")
        assert store.get_fact("k") == "v"
        assert store.get_fact("k", namespace="persona:alice") == "v2"

    def test_namespaced_set_get(self, store: MemoryStore):
        store.set_fact("favourite_color", "blue", namespace="persona:alice")
        store.set_fact("favourite_color", "red", namespace="persona:bob")
        assert store.get_fact("favourite_color", namespace="persona:alice") == "blue"
        assert store.get_fact("favourite_color", namespace="persona:bob") == "red"
        # Global namespace doesn't see them
        assert store.get_fact("favourite_color") is None

    def test_get_facts_filters_by_namespace(self, store: MemoryStore):
        store.set_fact("a", "1", namespace="persona:alice")
        store.set_fact("b", "2", namespace="persona:alice")
        store.set_fact("c", "3", namespace="persona:bob")
        store.set_fact("d", "4")  # global

        alice_facts = store.get_facts(namespace="persona:alice")
        assert len(alice_facts) == 2
        keys = {f.key for f in alice_facts}
        assert keys == {"a", "b"}

    def test_get_facts_global_default(self, store: MemoryStore):
        store.set_fact("g1", "1")
        store.set_fact("p1", "2", namespace="persona:alice")
        global_facts = store.get_facts()
        assert len(global_facts) == 1
        assert global_facts[0].key == "g1"

    def test_get_facts_all_namespaces(self, store: MemoryStore):
        store.set_fact("g", "global")
        store.set_fact("a", "alice", namespace="persona:alice")
        store.set_fact("b", "bob", namespace="persona:bob")
        all_facts = store.get_facts(namespace=None)
        assert len(all_facts) == 3

    def test_delete_fact_namespaced(self, store: MemoryStore):
        store.set_fact("k", "v1", namespace="persona:alice")
        store.set_fact("k", "v2", namespace="persona:bob")
        assert store.delete_fact("k", namespace="persona:alice") is True
        assert store.get_fact("k", namespace="persona:alice") is None
        # bob's still there
        assert store.get_fact("k", namespace="persona:bob") == "v2"

    def test_search_facts_namespaced(self, store: MemoryStore):
        store.set_fact("x", "alice loves cats", namespace="persona:alice")
        store.set_fact("x", "bob loves dogs", namespace="persona:bob")
        results = store.search_facts("loves", namespace="persona:alice")
        assert len(results) == 1
        assert results[0].value == "alice loves cats"

    def test_search_facts_all_namespaces(self, store: MemoryStore):
        store.set_fact("x", "shared term", namespace="persona:alice")
        store.set_fact("y", "shared term", namespace="persona:bob")
        results = store.search_facts("shared", namespace=None)
        assert len(results) == 2

    def test_prefix_filter_within_namespace(self, store: MemoryStore):
        store.set_fact("user.name", "alice", namespace="persona:alice")
        store.set_fact("user.role", "writer", namespace="persona:alice")
        store.set_fact("config.x", "1", namespace="persona:alice")
        results = store.get_facts(prefix="user.", namespace="persona:alice")
        assert len(results) == 2

    def test_legacy_no_namespace_kwarg_still_works(self, store: MemoryStore):
        # Backward compat: calling without namespace kwarg targets global
        store.set_fact("legacy_key", "legacy_value")
        assert store.get_fact("legacy_key") == "legacy_value"
        assert store.delete_fact("legacy_key") is True
