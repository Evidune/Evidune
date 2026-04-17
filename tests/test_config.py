"""Tests for core/config.py."""

from pathlib import Path

import pytest
import yaml

from core.config import load_config


def _write_yaml(data: dict, path: Path) -> Path:
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


class TestLoadConfig:
    def test_minimal_config(self, tmp_path: Path):
        cfg_path = _write_yaml({"domain": "test"}, tmp_path / "aiflay.yaml")
        config = load_config(cfg_path)
        assert config.domain == "test"
        assert config.metrics.adapter == "generic_csv"
        assert config.analysis.compare_window_days == 7

    def test_full_config(self, tmp_path: Path):
        data = {
            "domain": "zhihu",
            "description": "Test domain",
            "metrics": {
                "adapter": "generic_csv",
                "config": {"file": "data.csv", "title_field": "title"},
            },
            "references": [
                {
                    "path": "refs/case-studies.md",
                    "update_strategy": "replace_section",
                    "section": "## Top Performers",
                },
                {"path": "refs/hot.md", "update_strategy": "full_replace"},
            ],
            "analysis": {"compare_window_days": 14, "top_n": 3, "bottom_n": 2},
            "iteration": {"schedule": "0 21 * * *", "git_commit": False},
            "channels": [{"type": "stdout"}],
            "skills": {"directories": ["skills"], "prompt_mode": "index"},
            "agent": {
                "harness": {
                    "environment": {"runtime_dir": ".aiflay/runtime", "startup_timeout_s": 12},
                    "validation": {"headless": False, "slow_mo_ms": 50},
                    "delivery": {"branch_prefix": "feature/", "github_enabled": False},
                }
            },
        }
        config = load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))
        assert config.domain == "zhihu"
        assert len(config.references) == 2
        assert config.references[0].update_strategy == "replace_section"
        assert config.references[0].section == "## Top Performers"
        assert config.analysis.top_n == 3
        assert config.iteration.git_commit is False
        assert len(config.channels) == 1
        assert config.skills.prompt_mode == "index"
        assert config.agent is not None
        assert config.agent.harness.environment.runtime_dir == ".aiflay/runtime"
        assert config.agent.harness.environment.startup_timeout_s == 12
        assert config.agent.harness.validation.headless is False
        assert config.agent.harness.validation.slow_mo_ms == 50
        assert config.agent.harness.delivery.branch_prefix == "feature/"
        assert config.agent.harness.delivery.github_enabled is False

    def test_env_expansion(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TEST_WEBHOOK", "https://feishu.example.com/hook/abc")
        data = {
            "domain": "test",
            "channels": [{"type": "feishu", "webhook": "${TEST_WEBHOOK}"}],
        }
        config = load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))
        assert config.channels[0].webhook == "https://feishu.example.com/hook/abc"

    def test_env_expansion_missing_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        data = {
            "domain": "test",
            "channels": [{"type": "feishu", "webhook": "${NONEXISTENT_VAR}"}],
        }
        with pytest.raises(ValueError, match="Environment variable"):
            load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))

    def test_missing_domain_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="domain"):
            load_config(_write_yaml({"description": "no domain"}, tmp_path / "aiflay.yaml"))

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/aiflay.yaml")

    def test_invalid_strategy_raises(self, tmp_path: Path):
        data = {
            "domain": "test",
            "references": [{"path": "x.md", "update_strategy": "invalid"}],
        }
        with pytest.raises(ValueError, match="Invalid update_strategy"):
            load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))

    def test_replace_section_without_section_raises(self, tmp_path: Path):
        data = {
            "domain": "test",
            "references": [{"path": "x.md", "update_strategy": "replace_section"}],
        }
        with pytest.raises(ValueError, match="requires a 'section'"):
            load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))

    def test_invalid_skill_prompt_mode_raises(self, tmp_path: Path):
        data = {
            "domain": "test",
            "skills": {"prompt_mode": "invalid"},
        }
        with pytest.raises(ValueError, match="Invalid prompt_mode"):
            load_config(_write_yaml(data, tmp_path / "aiflay.yaml"))
