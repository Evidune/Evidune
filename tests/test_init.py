"""Tests for local project scaffolding and runtime path resolution."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.config import load_config
from core.loop import main
from core.project_init import init_project
from memory.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent


class TestInitProject:
    def test_init_scaffolds_runnable_local_project(self, tmp_path: Path):
        project_dir = tmp_path / "demo"

        exit_code = main(["init", "--path", str(project_dir)])
        assert exit_code == 0
        assert (project_dir / "evidune.yaml").exists()
        assert (project_dir / "data" / "metrics.csv").exists()
        assert (project_dir / "skills" / "write-article" / "SKILL.md").exists()

        config = load_config(project_dir / "evidune.yaml")
        assert config.memory.path == ".evidune/memory.db"
        assert config.agent is not None
        assert config.agent.emergence.output_dir == ".evidune/emerged_skills"

        exit_code = main(["run", "--config", str(project_dir / "evidune.yaml")])
        assert exit_code == 0

        skill_content = (project_dir / "skills" / "write-article" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "Auto-updated by evidune" in skill_content

        store = MemoryStore(project_dir / ".evidune" / "memory.db")
        try:
            runs = store.list_iteration_runs()
            assert len(runs) == 1
        finally:
            store.close()

    def test_init_project_refuses_to_overwrite_existing_files(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "evidune.yaml").write_text("domain: existing\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Refusing to overwrite"):
            init_project(project_dir)


def test_bundled_zhihu_example_runs_one_iteration(tmp_path: Path):
    copied = tmp_path / "zhihu"
    shutil.copytree(ROOT / "examples" / "zhihu", copied)

    exit_code = main(["run", "--config", str(copied / "evidune.yaml")])
    assert exit_code == 0

    assert (copied / ".evidune" / "zhihu-memory.db").exists()
    skill_content = (copied / "skills" / "write-article" / "SKILL.md").read_text(encoding="utf-8")
    assert "Auto-updated by evidune" in skill_content
