"""Tests for structured runtime self-management tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.runtime_tools import runtime_tools


def _write_config(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return _write_config(
        tmp_path / "evidune.yaml",
        {
            "domain": "agent",
            "agent": {
                "llm_provider": "openai",
                "tools": {
                    "external_enabled": True,
                    "self_management_enabled": True,
                },
            },
            "skills": {"directories": ["skills/"]},
            "identities": {"directories": ["identities/"]},
            "memory": {"path": str(tmp_path / "memory.db")},
            "gateways": [{"type": "cli"}],
        },
    )


@pytest.fixture
def tools(config_path: Path, tmp_path: Path):
    return {tool.name: tool for tool in runtime_tools(config_path=config_path, base_dir=tmp_path)}


class TestRuntimeTools:
    @pytest.mark.asyncio
    async def test_config_get_reads_root_and_dotted_path(self, tools):
        root = await tools["config_get"].handler()
        assert root["ok"] is True
        assert root["value"]["domain"] == "agent"

        value = await tools["config_get"].handler(path="agent.tools.external_enabled")
        assert value["ok"] is True
        assert value["value"] is True

    @pytest.mark.asyncio
    async def test_config_validate_returns_summary(self, tools):
        result = await tools["config_validate"].handler()
        assert result["ok"] is True
        assert result["domain"] == "agent"
        assert result["agent_configured"] is True
        assert result["gateway_count"] == 1

    @pytest.mark.asyncio
    async def test_config_patch_dry_run_does_not_write(self, tools, config_path: Path):
        result = await tools["config_patch"].handler(
            updates=[{"path": "agent.tools.external_enabled", "value": False}],
            dry_run=True,
            reason="test",
        )
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["restart_required"] is True

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert raw["agent"]["tools"]["external_enabled"] is True

    @pytest.mark.asyncio
    async def test_config_patch_apply_writes_backup_and_config(self, tools, config_path: Path):
        result = await tools["config_patch"].handler(
            updates=[
                {"path": "agent.tools.external_enabled", "value": False},
                {"path": "agent.tools.shell_timeout_s", "value": 5},
            ],
            dry_run=False,
            reason="tighten tools",
        )
        assert result["ok"] is True
        assert result["dry_run"] is False
        assert Path(result["backup_path"]).is_file()

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert raw["agent"]["tools"]["external_enabled"] is False
        assert raw["agent"]["tools"]["shell_timeout_s"] == 5

    @pytest.mark.asyncio
    async def test_config_patch_invalid_update_is_not_written(self, tools, config_path: Path):
        result = await tools["config_patch"].handler(
            updates=[{"path": "domain", "value": ""}],
            dry_run=False,
        )
        assert result["ok"] is False
        assert "domain" in result["error"]

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert raw["domain"] == "agent"

    @pytest.mark.asyncio
    async def test_request_runtime_restart_writes_marker(self, tools, tmp_path: Path):
        result = await tools["request_runtime_restart"].handler(
            reason="config changed",
            mode="restart",
        )
        assert result["ok"] is True
        marker_path = tmp_path / ".evidune" / "restart-request.json"
        assert result["marker_path"] == str(marker_path)

        payload = json.loads(marker_path.read_text(encoding="utf-8"))
        assert payload["kind"] == "runtime_restart_request"
        assert payload["mode"] == "restart"
        assert payload["reason"] == "config changed"
