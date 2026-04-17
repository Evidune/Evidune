"""CLI dispatch tests for env, validate, delivery, and maintenance commands."""

from __future__ import annotations

from pathlib import Path

import yaml

import core.loop as loop


def _write_config(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "domain": "demo",
                "agent": {
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_main_dispatches_env_command(tmp_path: Path, monkeypatch):
    cfg = _write_config(tmp_path / "aiflay.yaml")
    monkeypatch.setattr(loop, "_handle_env_command", lambda *args, **kwargs: 7)
    assert loop.main(["env", "status", "env-1", "--config", str(cfg)]) == 7


def test_main_dispatches_validate_command(tmp_path: Path, monkeypatch):
    cfg = _write_config(tmp_path / "aiflay.yaml")

    async def fake_validate(*args, **kwargs):
        return 8

    monkeypatch.setattr(loop, "_handle_validate_command", fake_validate)
    assert loop.main(["validate", "run", "--config", str(cfg)]) == 8


def test_main_dispatches_delivery_command(tmp_path: Path, monkeypatch):
    cfg = _write_config(tmp_path / "aiflay.yaml")
    monkeypatch.setattr(loop, "_handle_delivery_command", lambda *args, **kwargs: 9)
    assert loop.main(["delivery", "submit", "--config", str(cfg)]) == 9


def test_main_dispatches_maintenance_command(tmp_path: Path, monkeypatch):
    cfg = _write_config(tmp_path / "aiflay.yaml")
    monkeypatch.setattr(loop, "_handle_maintenance_command", lambda *args, **kwargs: 10)
    assert loop.main(["maintenance", "sweep", "--config", str(cfg)]) == 10
