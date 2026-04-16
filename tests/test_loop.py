"""Tests for iteration loop persistence and CLI inspection."""

from pathlib import Path

import yaml

from core.config import load_config
from core.loop import _load_active_emerged_skills, main, run_iteration
from memory.store import MemoryStore
from skills.registry import SkillRegistry


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _setup_iteration_project(tmp_path: Path) -> Path:
    data_path = _write(
        tmp_path / "data.csv",
        "title,reads,upvotes\n"
        "Golden Article,5000,200\n"
        "Decent Article,1500,60\n"
        "Flop Article,80,2\n",
    )

    _write(
        tmp_path / "skills" / "write-article" / "SKILL.md",
        "---\n"
        "name: write-article\n"
        "description: Write compelling articles\n"
        "outcome_metrics: true\n"
        "---\n"
        "## Instructions\n"
        "Write strong articles.\n"
        "\n"
        "## Reference Data\n"
        "placeholder\n",
    )
    _write(
        tmp_path / "refs" / "case-studies.md",
        "## Top Performers\n" "placeholder\n",
    )

    config = {
        "domain": "zhihu",
        "metrics": {
            "adapter": "generic_csv",
            "config": {
                "file": str(data_path),
                "title_field": "title",
                "metric_fields": ["reads", "upvotes"],
                "sort_metric": "reads",
            },
        },
        "references": [
            {
                "path": "refs/case-studies.md",
                "update_strategy": "replace_section",
                "section": "## Top Performers",
            }
        ],
        "skills": {"directories": ["skills/"], "auto_update": True},
        "memory": {"path": str(tmp_path / "memory.db")},
        "iteration": {"git_commit": False},
        "channels": [],
    }
    cfg_path = tmp_path / "aiflay.yaml"
    cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return cfg_path


class TestIterationLedger:
    def test_run_iteration_persists_ledger(self, tmp_path: Path):
        cfg_path = _setup_iteration_project(tmp_path)
        config = load_config(cfg_path)

        report = run_iteration(config, base_dir=tmp_path)

        store = MemoryStore(tmp_path / "memory.db")
        try:
            runs = store.list_iteration_runs()
            assert len(runs) == 1
            run = store.get_iteration_run(runs[0]["id"])
        finally:
            store.close()

        assert report.extra["iteration_run_id"] == runs[0]["id"]
        assert run is not None
        assert run["domain"] == "zhihu"
        assert run["metrics_adapter"] == "generic_csv"
        assert run["metrics_source"] == str(tmp_path / "data.csv")
        assert run["commit_sha"] is None
        assert any(
            update["path"].endswith("skills/write-article/SKILL.md") and update["has_changes"]
            for update in run["updates"]
        )
        assert any(
            update["path"].endswith("refs/case-studies.md") and update["has_changes"]
            for update in run["updates"]
        )

    def test_iterations_cli_list_and_show(self, tmp_path: Path, capsys):
        cfg_path = _setup_iteration_project(tmp_path)
        config = load_config(cfg_path)
        report = run_iteration(config, base_dir=tmp_path)

        exit_code = main(["iterations", "list", "--config", str(cfg_path)])
        assert exit_code == 0
        listed = capsys.readouterr().out
        assert "zhihu" in listed
        assert "adapter=generic_csv" in listed

        exit_code = main(
            ["iterations", "show", str(report.extra["iteration_run_id"]), "--config", str(cfg_path)]
        )
        assert exit_code == 0
        shown = capsys.readouterr().out
        assert f"Iteration Run #{report.extra['iteration_run_id']}" in shown
        assert "Patterns:" in shown
        assert "Updates:" in shown

    def test_load_active_emerged_skills_from_persisted_metadata(self, tmp_path: Path):
        emerged_path = _write(
            tmp_path / ".aiflay" / "emerged_skills" / "explain-topic" / "SKILL.md",
            "---\nname: explain-topic\ndescription: Explain\n---\n\n## Instructions\nDo it.\n",
        )
        store = MemoryStore(tmp_path / "memory.db")
        try:
            store.register_emerged_skill(
                name="explain-topic",
                status="active",
                path=str(emerged_path),
            )
            registry = SkillRegistry()
            loaded = _load_active_emerged_skills(registry, store, emerged_path.parent.parent)
        finally:
            store.close()

        assert loaded == 1
        assert registry.get("explain-topic") is not None
