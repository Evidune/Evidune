"""Tests for Feishu's official one-click app registration flow."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import yaml

import core.feishu_onboarding as onboarding
from core.loop import main


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {"domain": "demo", "agent": {}, "channels": [{"type": "stdout"}]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _fake_registration(monkeypatch, *, brand: str = "feishu"):
    captured = {}
    opened = []

    def register_app(**kwargs):
        captured.update(kwargs)
        kwargs["on_qr_code"](
            {
                "url": "https://open.feishu.cn/page/launcher?user_code=test",
                "expire_in": 600,
            }
        )
        return {
            "client_id": "cli_registered",
            "client_secret": "secret_registered",
            "user_info": {"tenant_brand": brand},
        }

    monkeypatch.setattr(
        onboarding,
        "load_lark",
        lambda: SimpleNamespace(register_app=register_app),
    )
    monkeypatch.setattr(
        onboarding.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )
    return captured, opened


def test_channels_add_feishu_one_click_persists_credentials(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    captured, opened = _fake_registration(monkeypatch)
    config_path = tmp_path / "evidune.yaml"
    _write_config(config_path)

    exit_code = main(
        [
            "channels",
            "add",
            "feishu",
            "--one-click",
            "--config",
            str(config_path),
        ]
    )

    assert exit_code == 0
    assert captured["source"] == "evidune"
    assert captured["create_only"] is True
    assert captured["app_preset"]["name"] == "Evidune · demo"
    assert opened == ["https://open.feishu.cn/page/launcher?user_code=test"]

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["gateways"] == [
        {
            "type": "feishu_bot",
            "app_id": "${FEISHU_APP_ID}",
            "app_secret": "${FEISHU_APP_SECRET}",
            "domain": "https://open.feishu.cn",
            "reply_mode": "card",
        }
    ]

    credentials_path = tmp_path / ".evidune" / "credentials.json"
    assert json.loads(credentials_path.read_text(encoding="utf-8")) == {
        "FEISHU_APP_ID": "cli_registered",
        "FEISHU_APP_SECRET": "secret_registered",
    }
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600

    output = capsys.readouterr().out
    assert "https://open.feishu.cn/page/launcher?user_code=test" in output
    assert "secret_registered" not in output

    monkeypatch.delenv("FEISHU_APP_ID")
    monkeypatch.delenv("FEISHU_APP_SECRET")
    assert main(["gateway", "status", "--config", str(config_path)]) == 0
    assert onboarding.load_local_credentials(config_path) == credentials_path


def test_onboard_one_click_uses_lark_domain(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    _fake_registration(monkeypatch, brand="lark")
    config_path = tmp_path / "evidune.yaml"

    exit_code = main(
        [
            "onboard",
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--channel",
            "feishu",
            "--one-click",
            "--no-open-browser",
            "--non-interactive",
            "--config",
            str(config_path),
        ]
    )

    assert exit_code == 0
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["gateways"][-1]["domain"] == "https://open.larksuite.com"
