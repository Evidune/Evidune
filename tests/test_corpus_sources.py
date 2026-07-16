from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from adapters.benchmark import CorpusSource, load_evaluation_corpus, validate_evaluation_corpus
from adapters.skill_catalog import catalog_source_root, load_skill_catalog, validate_skill_catalog
from core.corpus_sources import sync_corpus_sources, sync_pinned_sources


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_git_corpus_source_sync_is_commit_pinned_and_locked(tmp_path: Path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run(upstream, "init")
    _run(upstream, "config", "user.name", "Test")
    _run(upstream, "config", "user.email", "test@example.test")
    skill = upstream / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: sample\n---\nDo it.\n", encoding="utf-8")
    _run(upstream, "add", "skills/sample/SKILL.md")
    _run(upstream, "commit", "-m", "fixture")
    commit = _run(upstream, "rev-parse", "HEAD")

    manifest = tmp_path / "corpus.yaml"
    manifest.write_text(
        f"""corpus_id: real-source-fixture
schema_version: 1
adapter: {{id: fixture, revision: v1}}
sources:
  - name: upstream-skill
    mode: git
    url: {upstream}
    commit: {commit}
    license: MIT
    paths: [skills/sample/SKILL.md]
tasks:
  - id: task
    instruction: Do it
splits: {{development: [task]}}
evaluator: {{holdout_visibility: hidden}}
""",
        encoding="utf-8",
    )
    corpus = load_evaluation_corpus(manifest)
    source_root = tmp_path / "managed" / "sources"

    lock = sync_corpus_sources(corpus, source_root)
    validation = validate_evaluation_corpus(corpus, source_root=source_root)

    assert validation.ok
    assert lock["sources"][0]["commit"] == commit
    assert lock["sources"][0]["files"]["skills/sample/SKILL.md"]
    saved = json.loads((source_root.parent / "source-lock.json").read_text())
    assert saved["snapshot_id"] == corpus.corpus_id
    assert saved["manifest_digest"] == corpus.manifest_digest
    assert saved["retrieved_at"].endswith("+00:00")


def test_official_skill_catalog_keeps_source_only_skills_out_of_release_gate(tmp_path: Path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run(upstream, "init")
    _run(upstream, "config", "user.name", "Test")
    _run(upstream, "config", "user.email", "test@example.test")
    skill = upstream / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: sample\n---\nDo it.\n", encoding="utf-8")
    _run(upstream, "add", "skills/sample/SKILL.md")
    _run(upstream, "commit", "-m", "fixture")
    commit = _run(upstream, "rev-parse", "HEAD")
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        f"""catalog_id: official-test
schema_version: 1
sources:
  - name: upstream
    mode: git
    url: {upstream}
    commit: {commit}
    license: MIT
    paths: [skills/sample/SKILL.md]
skills:
  - name: sample
    source: upstream
    path: skills/sample/SKILL.md
    review_status: source_only
""",
        encoding="utf-8",
    )
    catalog = load_skill_catalog(catalog_path)
    source_root = catalog_source_root(catalog, tmp_path)
    sync_pinned_sources(
        snapshot_id=catalog.catalog_id,
        manifest_digest=catalog.manifest_digest,
        sources=catalog.sources,
        source_root=source_root,
    )

    validation = validate_skill_catalog(catalog, source_root)

    assert validation.ok
    assert validation.skills[0]["name"] == "sample"
    assert any("no approved executable fixture" in warning for warning in validation.warnings)


def test_approved_catalog_skill_requires_source_matched_fixture_pairing(tmp_path: Path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run(upstream, "init")
    _run(upstream, "config", "user.name", "Test")
    _run(upstream, "config", "user.email", "test@example.test")
    skill = upstream / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: sample\ndescription: sample\n---\nDo it.\n", encoding="utf-8")
    _run(upstream, "add", "skills/sample/SKILL.md")
    _run(upstream, "commit", "-m", "fixture")
    commit = _run(upstream, "rev-parse", "HEAD")
    fixture = tmp_path / "fixture.yaml"
    fixture.write_text(
        f"""corpus_id: approved-fixture
schema_version: 1
adapter: {{id: fixture, revision: v1}}
sources:
  - name: upstream
    mode: git
    url: {upstream}
    commit: {commit}
    license: MIT
    paths: [skills/sample/SKILL.md]
tasks:
  - {{id: task, instruction: Do it}}
splits: {{development: [task]}}
pairings:
  - skill: sample
    tasks: [task]
evaluator: {{holdout_visibility: hidden}}
""",
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        f"""catalog_id: approved-test
schema_version: 1
sources:
  - name: upstream
    mode: git
    url: {upstream}
    commit: {commit}
    license: MIT
    paths: [skills/sample/SKILL.md]
skills:
  - name: sample
    source: upstream
    path: skills/sample/SKILL.md
    review_status: approved
    fixture_manifest: fixture.yaml
    fixture_task_ids: [task]
""",
        encoding="utf-8",
    )
    catalog = load_skill_catalog(catalog_path)
    source_root = catalog_source_root(catalog, tmp_path)
    sync_pinned_sources(
        snapshot_id=catalog.catalog_id,
        manifest_digest=catalog.manifest_digest,
        sources=catalog.sources,
        source_root=source_root,
    )

    validation = validate_skill_catalog(catalog, source_root)

    assert validation.ok
    assert validation.warnings == []
    assert validation.skills[0]["fixture_task_ids"] == ["task"]


def test_source_sync_rejects_git_option_injection(tmp_path: Path):
    with pytest.raises(ValueError, match="unsafe URL"):
        sync_pinned_sources(
            snapshot_id="unsafe",
            manifest_digest="digest",
            sources=[
                CorpusSource(
                    name="unsafe",
                    mode="git",
                    url="--upload-pack=malicious",
                    commit="a" * 40,
                    license="MIT",
                )
            ],
            source_root=tmp_path / "sources",
        )
