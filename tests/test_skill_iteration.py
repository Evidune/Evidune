"""End-to-end integration tests for skill self-iteration.

Verifies Aiflay's core differentiator: SKILL.md files with outcome_metrics: true
get their Reference Data section auto-updated by the iteration loop, based on
real-world business metrics.
"""

from pathlib import Path

import yaml

from core.config import load_config
from core.loop import run_iteration


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _setup_project(tmp_path: Path, auto_update: bool = True) -> Path:
    """Create a minimal project layout for integration testing."""
    # Metrics CSV
    _write(
        tmp_path / "data.csv",
        "title,reads,upvotes\n"
        "Golden Article,5000,200\n"
        "Decent Article,1500,60\n"
        "Mediocre Article,400,12\n"
        "Flop Article,80,2\n",
    )

    # Skill WITH outcome_metrics
    _write(
        tmp_path / "skills" / "write-article" / "SKILL.md",
        "---\n"
        "name: write-article\n"
        "description: Write compelling articles\n"
        "outcome_metrics: true\n"
        "---\n"
        "## Instructions\n"
        "Write good stuff.\n"
        "\n"
        "## Reference Data\n"
        "(placeholder — will be replaced by aiflay)\n"
        "\n"
        "## Footer\n"
        "Do not touch this section.\n",
    )

    # Skill WITHOUT outcome_metrics — must NOT be modified
    _write(
        tmp_path / "skills" / "other-skill" / "SKILL.md",
        "---\n"
        "name: other-skill\n"
        "description: Some other skill\n"
        "---\n"
        "## Instructions\n"
        "Do other things.\n"
        "\n"
        "## Reference Data\n"
        "(this should NOT be touched)\n",
    )

    # aiflay.yaml
    config = {
        "domain": "test-domain",
        "metrics": {
            "adapter": "generic_csv",
            "config": {
                "file": str(tmp_path / "data.csv"),
                "title_field": "title",
                "metric_fields": ["reads", "upvotes"],
                "sort_metric": "reads",
            },
        },
        "analysis": {"top_n": 2, "bottom_n": 1},
        "iteration": {"git_commit": False},
        "skills": {
            "directories": ["skills/"],
            "auto_update": auto_update,
        },
        "memory": {"path": str(tmp_path / "memory.db")},
        "channels": [],
    }
    cfg_path = tmp_path / "aiflay.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)

    return cfg_path


class TestSkillSelfIteration:
    def test_updates_outcome_skill_reference_data(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path)
        config = load_config(cfg_path)

        run_iteration(config, base_dir=tmp_path)

        skill_content = (tmp_path / "skills" / "write-article" / "SKILL.md").read_text()

        # Reference Data section should be replaced with real data
        assert "Golden Article" in skill_content
        assert "Decent Article" in skill_content
        assert "reads=5000.0" in skill_content
        assert "Auto-updated by aiflay" in skill_content

        # Placeholder text is gone
        assert "(placeholder — will be replaced by aiflay)" not in skill_content

        # Other sections are preserved
        assert "## Instructions" in skill_content
        assert "Write good stuff." in skill_content
        assert "## Footer" in skill_content
        assert "Do not touch this section." in skill_content

    def test_skips_skills_without_outcome_metrics(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path)
        config = load_config(cfg_path)

        original = (tmp_path / "skills" / "other-skill" / "SKILL.md").read_text()
        run_iteration(config, base_dir=tmp_path)
        after = (tmp_path / "skills" / "other-skill" / "SKILL.md").read_text()

        # Must be completely untouched
        assert original == after
        assert "(this should NOT be touched)" in after

    def test_auto_update_false_skips_all_skills(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path, auto_update=False)
        config = load_config(cfg_path)

        original = (tmp_path / "skills" / "write-article" / "SKILL.md").read_text()
        run_iteration(config, base_dir=tmp_path)
        after = (tmp_path / "skills" / "write-article" / "SKILL.md").read_text()

        # Even the outcome_metrics skill is untouched when auto_update=false
        assert original == after

    def test_report_includes_skill_updates(self, tmp_path: Path):
        cfg_path = _setup_project(tmp_path)
        config = load_config(cfg_path)

        report = run_iteration(config, base_dir=tmp_path)

        # The update list should include the skill file that was changed
        changed_paths = [u.path for u in report.updates if u.has_changes]
        assert any("write-article/SKILL.md" in p for p in changed_paths)
        assert not any("other-skill" in p for p in changed_paths)

    def test_custom_update_section(self, tmp_path: Path):
        # Skill with custom update_section frontmatter
        _write(tmp_path / "data.csv", "title,reads\nA,100\nB,50\n")

        _write(
            tmp_path / "skills" / "custom" / "SKILL.md",
            "---\n"
            "name: custom\n"
            "description: Custom section skill\n"
            "outcome_metrics: true\n"
            'update_section: "## My Custom Section"\n'
            "---\n"
            "## Instructions\n"
            "Do things.\n"
            "\n"
            "## My Custom Section\n"
            "(old data here)\n"
            "\n"
            "## Reference Data\n"
            "This one should NOT be updated.\n",
        )

        config_data = {
            "domain": "test",
            "metrics": {
                "adapter": "generic_csv",
                "config": {
                    "file": str(tmp_path / "data.csv"),
                    "metric_fields": ["reads"],
                    "sort_metric": "reads",
                },
            },
            "iteration": {"git_commit": False},
            "skills": {"directories": ["skills/"], "auto_update": True},
            "memory": {"path": str(tmp_path / "memory.db")},
            "channels": [],
        }
        cfg_path = tmp_path / "aiflay.yaml"
        with open(cfg_path, "w") as f:
            yaml.dump(config_data, f)

        config = load_config(cfg_path)
        run_iteration(config, base_dir=tmp_path)

        skill_content = (tmp_path / "skills" / "custom" / "SKILL.md").read_text()

        # Custom section was updated
        assert "Auto-updated by aiflay" in skill_content
        assert "A" in skill_content  # Top performer
        assert "(old data here)" not in skill_content

        # Default "## Reference Data" section was NOT touched
        assert "This one should NOT be updated." in skill_content
