from __future__ import annotations

import sys
import types

import pytest

from core.appworld_corpus import build_appworld_manifest


def test_appworld_manifest_uses_real_task_ids_and_hidden_evaluator(monkeypatch):
    module = types.ModuleType("appworld")
    module.load_task_ids = lambda dataset: ["task-1", "task-2", "task-3"]
    monkeypatch.setitem(sys.modules, "appworld", module)

    payload = build_appworld_manifest(
        dataset="dev",
        split="holdout",
        source_commit="a" * 40,
        limit=2,
    )

    assert [task["id"] for task in payload["tasks"]] == ["task-1", "task-2"]
    assert payload["splits"]["holdout"] == ["task-1", "task-2"]
    assert payload["evaluator"]["holdout_visibility"] == "hidden"
    assert payload["evaluator"]["minimum_valid_trials"] == 3
    assert payload["evaluator"]["require_live_model"] is True
    assert payload["budget"]["max_model_turns_per_trial"] == 18
    assert payload["sources"][0]["commit"] == "a" * 40
    assert payload["environment"]["protected_data"] is True
    assert payload["corpus_id"].endswith("-25c9cf2cb832")


def test_appworld_manifest_identity_includes_selected_task_set(monkeypatch):
    module = types.ModuleType("appworld")
    module.load_task_ids = lambda dataset: ["task-1", "task-2", "task-3"]
    monkeypatch.setitem(sys.modules, "appworld", module)

    two_tasks = build_appworld_manifest(
        dataset="dev", split="holdout", source_commit="a" * 40, limit=2
    )
    three_tasks = build_appworld_manifest(
        dataset="dev", split="holdout", source_commit="a" * 40, limit=3
    )

    assert two_tasks["corpus_id"] != three_tasks["corpus_id"]


def test_appworld_manifest_rejects_non_pinned_source(monkeypatch):
    with pytest.raises(ValueError, match="40-character"):
        build_appworld_manifest(
            dataset="dev",
            split="development",
            source_commit="main",
        )
