"""Tests for pinned corpus manifests and generic benchmark adapters."""

import hashlib
from pathlib import Path

import pytest

from adapters.benchmark import load_evaluation_corpus, validate_evaluation_corpus


def _manifest(
    tmp_path: Path, *, split_overlap: bool = False, visible_holdout: bool = False
) -> Path:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: test\ndescription: test\n---\n\nDo the task.\n", encoding="utf-8")
    digest = hashlib.sha256(skill.read_bytes()).hexdigest()
    overlap = "\n    - task-1" if split_overlap else ""
    visibility = "visible" if visible_holdout else "hidden"
    manifest = tmp_path / "corpus.yaml"
    manifest.write_text(
        f"""corpus_id: fixture-v1
schema_version: 1
adapter:
  id: fixture
  revision: v1
sources:
  - name: official-test-skill
    url: https://example.test/skills
    commit: abc123
    license: MIT
    paths: [SKILL.md]
    digest: {digest}
tasks:
  - id: task-1
    instruction: Complete the task
    initial_state: {{done: false}}
    expected_state: {{done: true}}
  - id: task-2
    instruction: Complete the holdout task
    initial_state: {{done: false}}
    expected_state: {{done: true}}
splits:
  development: [task-1]
  holdout:
    - task-2{overlap}
pairings:
  - skill: official-test-skill
    tasks: [task-1]
    required_capabilities: [state-update]
    tool_compatibility: {{fixture: direct}}
environment: {{kind: fixture}}
evaluator:
  holdout_visibility: {visibility}
  required_evaluators: [fixture_state_and_output]
  minimum_attribution: direct
budget:
  max_model_calls: 20
""",
        encoding="utf-8",
    )
    return manifest


def test_load_and_validate_pinned_corpus(tmp_path: Path):
    corpus = load_evaluation_corpus(_manifest(tmp_path))
    result = validate_evaluation_corpus(corpus)

    assert result.ok is True
    assert result.task_count == 2
    assert corpus.task_refs("development")[0].id == "task-1"
    assert corpus.manifest_digest


def test_split_overlap_and_visible_holdout_are_rejected(tmp_path: Path):
    corpus = load_evaluation_corpus(_manifest(tmp_path, split_overlap=True, visible_holdout=True))

    result = validate_evaluation_corpus(corpus)

    assert result.ok is False
    assert any("appears in both" in error for error in result.errors)
    assert any("visibility must be hidden" in error for error in result.errors)


def test_unknown_split_is_rejected(tmp_path: Path):
    corpus = load_evaluation_corpus(_manifest(tmp_path))
    with pytest.raises(ValueError, match="Unknown corpus split"):
        corpus.task_refs("missing")


def test_skill_pairing_limits_executable_tasks(tmp_path: Path):
    corpus = load_evaluation_corpus(_manifest(tmp_path))

    assert [
        task.id for task in corpus.task_refs("development", skill_name="official-test-skill")
    ] == ["task-1"]
    with pytest.raises(ValueError, match="No reviewed task pairing"):
        corpus.task_refs("development", skill_name="unreviewed-skill")
