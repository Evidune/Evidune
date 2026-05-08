"""Tests for OpenClaw-style configuration CLI flows."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.config import load_config
from core.loop import main


def _write_config(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _read_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_configure_model_preserves_unrelated_sections(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg_path = tmp_path / "evidune.yaml"
    _write_config(
        cfg_path,
        {
            "domain": "demo",
            "metrics": {"adapter": "generic_csv", "config": {"file": "data.csv"}},
            "skills": {"directories": ["project-skills/"]},
            "memory": {"path": ".evidune/memory.db"},
            "agent": {"llm_provider": "openai", "llm_model": "gpt-4o"},
        },
    )

    exit_code = main(
        [
            "configure",
            "--section",
            "model",
            "--provider",
            "openai-compatible",
            "--model",
            "qwen/deepseek-v4-flash",
            "--base-url",
            "https://openrouter.ai/api/v1",
            "--api-key-env",
            "OPENROUTER_API_KEY",
            "--non-interactive",
            "--config",
            str(cfg_path),
        ]
    )

    assert exit_code == 0
    data = _read_config(cfg_path)
    assert data["agent"]["llm_provider"] == "openai-compatible"
    assert data["agent"]["llm_model"] == "qwen/deepseek-v4-flash"
    assert data["agent"]["llm_base_url"] == "https://openrouter.ai/api/v1"
    assert data["agent"]["api_key_env"] == "OPENROUTER_API_KEY"
    assert data["metrics"] == {"adapter": "generic_csv", "config": {"file": "data.csv"}}
    assert data["skills"] == {"directories": ["project-skills/"]}
    assert data["memory"] == {"path": ".evidune/memory.db"}
    assert load_config(cfg_path).agent.llm_model == "qwen/deepseek-v4-flash"


def test_channels_add_web_and_feishu_write_valid_gateways(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    cfg_path = tmp_path / "evidune.yaml"
    _write_config(cfg_path, {"domain": "demo", "agent": {}, "channels": [{"type": "stdout"}]})

    assert (
        main(
            [
                "channels",
                "add",
                "web",
                "--host",
                "0.0.0.0",
                "--port",
                "8081",
                "--config",
                str(cfg_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "channels",
                "add",
                "feishu",
                "--app-id-env",
                "FEISHU_APP_ID",
                "--app-secret-env",
                "FEISHU_APP_SECRET",
                "--domain",
                "https://open.feishu.cn",
                "--reply-mode",
                "text",
                "--allowed-open-ids",
                "ou_1,ou_2",
                "--config",
                str(cfg_path),
            ]
        )
        == 0
    )

    data = _read_config(cfg_path)
    assert data["channels"] == [{"type": "stdout"}]
    assert data["gateways"] == [
        {"type": "web", "host": "0.0.0.0", "port": 8081},
        {
            "type": "feishu_bot",
            "app_id": "${FEISHU_APP_ID}",
            "app_secret": "${FEISHU_APP_SECRET}",
            "domain": "https://open.feishu.cn",
            "reply_mode": "text",
            "allowed_open_ids": ["ou_1", "ou_2"],
        },
    ]
    loaded = load_config(cfg_path)
    assert [gateway.type for gateway in loaded.gateways] == ["web", "feishu_bot"]


def test_channels_list_remove_are_idempotent(tmp_path: Path, capsys):
    cfg_path = tmp_path / "evidune.yaml"
    _write_config(
        cfg_path,
        {
            "domain": "demo",
            "agent": {},
            "gateways": [{"type": "cli"}, {"type": "web", "host": "127.0.0.1", "port": 8080}],
        },
    )

    assert main(["channels", "list", "--config", str(cfg_path)]) == 0
    listed = capsys.readouterr().out
    assert "cli" in listed
    assert "web" in listed

    assert main(["channels", "remove", "web", "--config", str(cfg_path)]) == 0
    assert main(["channels", "remove", "web", "--config", str(cfg_path)]) == 0

    data = _read_config(cfg_path)
    assert data["gateways"] == [{"type": "cli"}]


def test_missing_env_refs_are_written_and_status_fails_clearly(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("MISSING_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("MISSING_FEISHU_APP_SECRET", raising=False)
    cfg_path = tmp_path / "evidune.yaml"
    _write_config(cfg_path, {"domain": "demo", "agent": {}})

    assert (
        main(
            [
                "channels",
                "add",
                "feishu",
                "--app-id-env",
                "MISSING_FEISHU_APP_ID",
                "--app-secret-env",
                "MISSING_FEISHU_APP_SECRET",
                "--config",
                str(cfg_path),
            ]
        )
        == 0
    )
    data = _read_config(cfg_path)
    assert data["gateways"][0]["app_id"] == "${MISSING_FEISHU_APP_ID}"

    assert main(["gateway", "status", "--config", str(cfg_path)]) == 1
    output = capsys.readouterr().out
    assert "missing environment variables" in output
    assert "MISSING_FEISHU_APP_ID" in output
    assert "MISSING_FEISHU_APP_SECRET" in output


def test_onboard_non_interactive_creates_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    cfg_path = tmp_path / "evidune.yaml"

    exit_code = main(
        [
            "onboard",
            "--provider",
            "openai",
            "--model",
            "gpt-4.1-mini",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--channel",
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            "8081",
            "--non-interactive",
            "--config",
            str(cfg_path),
        ]
    )

    assert exit_code == 0
    data = _read_config(cfg_path)
    assert data["agent"]["llm_provider"] == "openai"
    assert data["agent"]["llm_model"] == "gpt-4.1-mini"
    assert data["agent"]["api_key_env"] == "OPENAI_API_KEY"
    assert data["gateways"] == [
        {"type": "cli"},
        {"type": "web", "host": "127.0.0.1", "port": 8081},
    ]
    assert data["channels"] == [{"type": "stdout"}]
    assert [gateway.type for gateway in load_config(cfg_path).gateways] == ["cli", "web"]
