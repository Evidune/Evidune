"""Tests for runtime environments, validation, delivery, maintenance, and harness tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.harness.delivery import DeliveryManager
from agent.harness.maintenance import MaintenanceSweepRunner
from agent.harness.runtime import HarnessRuntimeManager, RuntimeEnvironment
from agent.harness.validation import ValidationHarness
from agent.tools.harness_tools import harness_tools
from memory.store import MemoryStore
from tests.web_harness import start_web_harness


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "runtime-memory.db")
    yield store
    store.close()


def _environment(tmp_path: Path, task_id: str = "task-1") -> RuntimeEnvironment:
    manager = HarnessRuntimeManager(runtime_dir=tmp_path / "runtime", base_dir=tmp_path)
    return manager.create_environment(task_id)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_runtime_manager_creates_and_loads_environment(tmp_path: Path):
    manager = HarnessRuntimeManager(runtime_dir=tmp_path / "runtime", base_dir=tmp_path)
    environment = manager.create_environment("task-1")
    assert environment.root.is_dir()
    assert (environment.root / "environment.json").is_file()
    loaded = manager.load_environment(environment.environment_id)
    assert loaded.environment_id == environment.environment_id
    assert loaded.status()["service"]["status"] == "stopped"


@pytest.mark.asyncio
async def test_validation_harness_drives_real_web_gateway(tmp_path: Path):
    pytest.importorskip("playwright.async_api")
    web = start_web_harness(tmp_path / "browser-memory.db")
    try:
        environment = _environment(tmp_path, "task-validate")
        environment.service_port = int(web.base_url.rsplit(":", 1)[1])
        validator = ValidationHarness()
        opened = await validator.open_app(
            environment,
            session_id="browser",
            base_url=web.base_url,
        )
        snapshot = await validator.snapshot_ui(environment, session_id="browser")
        screenshot = await validator.capture_screenshot(
            environment,
            session_id="browser",
            name="browser-check",
        )
        assertion = await validator.assert_ui_state(
            environment,
            session_id="browser",
            visible_test_id="chat-input",
            contains_text="Evidune",
        )
        assert opened["url"].startswith(web.base_url)
        assert "chat-input" in snapshot["test_ids"]
        assert Path(screenshot["path"]).is_file()
        assert assertion["ok"] is True
        await validator.close_environment(environment.environment_id)
    finally:
        web.close()


def test_delivery_manager_commits_to_local_branch(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test User", cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    (repo / "README.md").write_text("hello world\n", encoding="utf-8")

    environment = RuntimeEnvironment(
        environment_id="env-delivery",
        task_id="task-delivery",
        root=tmp_path / "runtime" / "env-delivery",
        base_dir=repo,
    )
    manager = DeliveryManager(repo)
    result = manager.submit(environment, message="chore(harness): update readme")

    assert result["mode"] == "local"
    assert result["branch"].startswith("codex/")
    assert result["commit_sha"]
    assert "README.md" in result["changed_files"]


def test_maintenance_runner_returns_structured_issues(monkeypatch, tmp_path: Path):
    runner = MaintenanceSweepRunner(tmp_path)

    def fake_docs():
        from agent.harness.maintenance import SweepIssue

        return [SweepIssue("docs", "high", "Broken doc", "docs lint failed")]

    monkeypatch.setattr(runner, "_docs_issues", fake_docs)
    monkeypatch.setattr(runner, "_constraint_issues", lambda: [])
    monkeypatch.setattr(runner, "_skill_issues", lambda: [])
    monkeypatch.setattr(runner, "_docs_graph_issues", lambda: [])

    result = runner.sweep()
    assert result["issues"][0]["title"] == "Broken doc"
    assert result["suggested_tasks"][0]["priority"] == "high"


@pytest.mark.asyncio
async def test_harness_tools_update_task_metadata(memory: MemoryStore, tmp_path: Path):
    class FakeValidator:
        async def open_app(self, environment, **kwargs):
            return {"url": "http://example.test", "title": "Example", **kwargs}

        async def navigate_ui(self, environment, **kwargs):
            return {"url": "http://example.test/next", **kwargs}

        async def snapshot_ui(self, environment, **kwargs):
            return {"url": "http://example.test", "test_ids": ["chat-input"], **kwargs}

        async def capture_screenshot(self, environment, **kwargs):
            target = environment.artifacts_dir / "fake.png"
            target.write_bytes(b"png")
            return {"path": str(target), **kwargs}

        async def assert_ui_state(self, environment, **kwargs):
            return {"ok": False, "failures": ["missing text"], **kwargs}

    class FakeDelivery:
        def submit(self, environment, **kwargs):
            return {"mode": "local", "branch": "codex/test", "ci": {"status": "skipped"}}

        def list_review_comments(self, environment):
            return [{"id": 1, "body": "nit"}]

        def add_review_comment(self, environment, **kwargs):
            return {"id": 1, **kwargs}

        def respond_review_comment(self, environment, **kwargs):
            return {"id": kwargs["comment_id"], "response": kwargs["response"], "responded": True}

    class FakeMaintenance:
        def sweep(self):
            return {"issues": [{"title": "x"}], "suggested_tasks": []}

    memory.create_harness_task(task_id="task-1", conversation_id="conv-1")
    environment = _environment(tmp_path)
    tools = {
        tool.name: tool
        for tool in harness_tools(
            memory=memory,
            task_id="task-1",
            environment=environment,
            validator=FakeValidator(),
            delivery_manager=FakeDelivery(),
            maintenance_runner=FakeMaintenance(),
            allow_mutation=True,
        )
    }

    await tools["open_app"].handler()
    await tools["capture_screenshot"].handler()
    assertion = await tools["assert_ui_state"].handler(contains_text="missing")
    await tools["delivery_submit"].handler()
    await tools["maintenance_sweep"].handler()

    task = memory.get_harness_task("task-1")
    assert assertion["ok"] is False
    assert task["validation_summary"]["status"] == "failed"
    assert task["delivery_summary"]["mode"] == "local"
    assert "screenshot" in task["artifact_manifest"]
