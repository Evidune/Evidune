"""Tests for iteration loop persistence and CLI inspection."""

from pathlib import Path

import pytest
import yaml

import core.loop as loop_module
from core.config import load_config
from core.loop import _apply_skill_state_overrides, _load_active_emerged_skills, main, run_iteration
from core.project_init import _config_template
from core.runtime_paths import resolve_emergence_output_dir, resolve_memory_path
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
        "domain": "content",
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
    cfg_path = tmp_path / "evidune.yaml"
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
        assert run["domain"] == "content"
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
        assert "content" in listed
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
            tmp_path / ".evidune" / "emerged_skills" / "explain-topic" / "SKILL.md",
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

    def test_apply_skill_state_overrides_unregisters_non_active_skills(self, tmp_path: Path):
        base_path = _write(
            tmp_path / "skills" / "writer" / "SKILL.md",
            "---\nname: writer\ndescription: Write\n---\n\n## Instructions\nDo it.\n",
        )
        emerged_path = _write(
            tmp_path / ".evidune" / "emerged_skills" / "helper" / "SKILL.md",
            "---\nname: helper\ndescription: Help\n---\n\n## Instructions\nDo it.\n",
        )
        store = MemoryStore(tmp_path / "memory.db")
        try:
            store.upsert_skill_state(
                "writer", origin="base", path=str(base_path), status="disabled"
            )
            store.register_emerged_skill(name="helper", status="active", path=str(emerged_path))
            store.set_skill_state("helper", "pending_review")
            registry = SkillRegistry()
            registry.load_directory(tmp_path / "skills")
            _load_active_emerged_skills(registry, store, emerged_path.parent.parent)
            removed = _apply_skill_state_overrides(registry, store)
        finally:
            store.close()

        assert removed == 2
        assert registry.get("writer") is None
        assert registry.get("helper") is None

    def test_deploy_config_uses_persistent_runtime_paths(self):
        repo_root = Path(__file__).resolve().parents[1]
        cfg_path = repo_root / "examples" / "content" / "evidune.deploy.yaml"
        config = load_config(cfg_path)
        base_dir = cfg_path.parent

        memory_path = Path(resolve_memory_path(config, base_dir))
        emergence_path = Path(resolve_emergence_output_dir(config, base_dir))

        assert memory_path.is_absolute()
        assert emergence_path.is_absolute()
        assert base_dir.resolve() not in memory_path.parents
        assert base_dir.resolve() not in emergence_path.parents
        assert str(memory_path).endswith(".evidune-deploy/state/content/content-memory.db")
        assert str(emergence_path).endswith(".evidune-deploy/state/content/emerged_skills")

    @pytest.mark.asyncio
    async def test_serve_initialises_core_learning_subsystems_when_blocks_are_omitted(
        self, tmp_path: Path, monkeypatch
    ):
        cfg_path = _write(
            tmp_path / "evidune.yaml",
            yaml.safe_dump(
                {
                    "domain": "test",
                    "agent": {
                        "llm_provider": "openai",
                        "llm_model": "gpt-4o",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                    "skills": {"directories": ["skills/"]},
                    "identities": {"directories": ["identities/"]},
                    "memory": {"path": str(tmp_path / "memory.db")},
                    "gateways": [{"type": "cli"}],
                }
            ),
        )
        (tmp_path / "skills").mkdir()
        (tmp_path / "identities").mkdir()
        config = load_config(cfg_path)

        llm = object()
        captured: dict[str, object] = {}
        fact_judges: list[object] = []
        detector_judges: list[object] = []
        synth_calls: list[tuple[object, str]] = []
        loaded_dirs: list[str] = []

        class FakeFactExtractor:
            def __init__(self, judge):
                fact_judges.append(judge)

        class FakePatternDetector:
            def __init__(self, judge):
                detector_judges.append(judge)

        class FakeSkillSynthesizer:
            def __init__(self, judge, output_dir):
                synth_calls.append((judge, output_dir))

        class FakeTitleGenerator:
            def __init__(self, llm):
                self.llm = llm

        class FakeAgentCore:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeRouter:
            def __init__(self, agent, gateways):
                self.agent = agent
                self.gateways = gateways

            async def start(self):
                return None

            async def stop(self):
                return None

        monkeypatch.setattr("agent.llm.create_llm_client", lambda **kwargs: llm)
        monkeypatch.setattr("agent.fact_extractor.FactExtractor", FakeFactExtractor)
        monkeypatch.setattr("agent.pattern_detector.PatternDetector", FakePatternDetector)
        monkeypatch.setattr("agent.skill_synthesizer.SkillSynthesizer", FakeSkillSynthesizer)
        monkeypatch.setattr("agent.title_generator.TitleGenerator", FakeTitleGenerator)
        monkeypatch.setattr("agent.core.AgentCore", FakeAgentCore)
        monkeypatch.setattr("gateway.router.Router", FakeRouter)
        monkeypatch.setattr("gateway.router.create_gateway", lambda *args, **kwargs: object())
        monkeypatch.setattr(
            loop_module,
            "_build_harness_services",
            lambda *args, **kwargs: (None, None, None, None),
        )
        monkeypatch.setattr(
            loop_module,
            "_load_persisted_emerged_skills",
            lambda *args, **kwargs: loaded_dirs.append(str(args[2])) or 0,
        )

        await loop_module.serve(config, tmp_path, config_path=cfg_path)

        assert fact_judges == [llm]
        assert detector_judges == [llm]
        assert synth_calls == [(llm, str(Path.home() / ".evidune" / "emerged_skills"))]
        assert loaded_dirs == [str(Path.home() / ".evidune" / "emerged_skills")]
        assert captured["fact_extraction_every_n_turns"] == 5
        assert captured["fact_extraction_min_confidence"] == 0.7
        assert captured["emergence_every_n_turns"] == 10
        assert captured["emergence_min_confidence"] == 0.7
        assert captured["fact_extractor"] is not None
        assert captured["pattern_detector"] is not None
        assert captured["skill_synthesizer"] is not None

    @pytest.mark.asyncio
    async def test_serve_ignores_legacy_disabled_flags_for_core_learning_subsystems(
        self, tmp_path: Path, monkeypatch
    ):
        cfg_path = _write(
            tmp_path / "evidune.yaml",
            yaml.safe_dump(
                {
                    "domain": "test",
                    "agent": {
                        "llm_provider": "openai",
                        "llm_model": "gpt-4o",
                        "api_key_env": "OPENAI_API_KEY",
                        "fact_extraction": {
                            "enabled": False,
                            "every_n_turns": 7,
                            "min_confidence": 0.8,
                            "use_evaluator": False,
                        },
                        "emergence": {
                            "enabled": False,
                            "every_n_turns": 9,
                            "min_confidence": 0.65,
                            "use_evaluator": False,
                            "output_dir": ".evidune/custom-emerged",
                        },
                    },
                    "skills": {"directories": ["skills/"]},
                    "identities": {"directories": ["identities/"]},
                    "memory": {"path": str(tmp_path / "memory.db")},
                    "gateways": [{"type": "cli"}],
                }
            ),
        )
        (tmp_path / "skills").mkdir()
        (tmp_path / "identities").mkdir()
        config = load_config(cfg_path)

        llm = object()
        captured: dict[str, object] = {}
        fact_judges: list[object] = []
        detector_judges: list[object] = []
        synth_calls: list[tuple[object, str]] = []

        class FakeFactExtractor:
            def __init__(self, judge):
                fact_judges.append(judge)

        class FakePatternDetector:
            def __init__(self, judge):
                detector_judges.append(judge)

        class FakeSkillSynthesizer:
            def __init__(self, judge, output_dir):
                synth_calls.append((judge, output_dir))

        class FakeTitleGenerator:
            def __init__(self, llm):
                self.llm = llm

        class FakeAgentCore:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeRouter:
            def __init__(self, agent, gateways):
                self.agent = agent
                self.gateways = gateways

            async def start(self):
                return None

            async def stop(self):
                return None

        monkeypatch.setattr("agent.llm.create_llm_client", lambda **kwargs: llm)
        monkeypatch.setattr("agent.fact_extractor.FactExtractor", FakeFactExtractor)
        monkeypatch.setattr("agent.pattern_detector.PatternDetector", FakePatternDetector)
        monkeypatch.setattr("agent.skill_synthesizer.SkillSynthesizer", FakeSkillSynthesizer)
        monkeypatch.setattr("agent.title_generator.TitleGenerator", FakeTitleGenerator)
        monkeypatch.setattr("agent.core.AgentCore", FakeAgentCore)
        monkeypatch.setattr("gateway.router.Router", FakeRouter)
        monkeypatch.setattr("gateway.router.create_gateway", lambda *args, **kwargs: object())
        monkeypatch.setattr(
            loop_module,
            "_build_harness_services",
            lambda *args, **kwargs: (None, None, None, None),
        )
        monkeypatch.setattr(loop_module, "_load_persisted_emerged_skills", lambda *args: 0)

        await loop_module.serve(config, tmp_path, config_path=cfg_path)

        assert fact_judges == [llm]
        assert detector_judges == [llm]
        assert synth_calls == [(llm, str((tmp_path / ".evidune" / "custom-emerged").resolve()))]
        assert captured["fact_extraction_every_n_turns"] == 7
        assert captured["fact_extraction_min_confidence"] == 0.8
        assert captured["emergence_every_n_turns"] == 9
        assert captured["emergence_min_confidence"] == 0.65
        assert captured["fact_extractor"] is not None
        assert captured["pattern_detector"] is not None
        assert captured["skill_synthesizer"] is not None

    def test_examples_and_init_template_do_not_emit_core_enabled_flags(self):
        repo_root = Path(__file__).resolve().parents[1]
        example_text = (repo_root / "examples" / "content" / "evidune.yaml").read_text(
            encoding="utf-8"
        )
        deploy_text = (repo_root / "examples" / "content" / "evidune.deploy.yaml").read_text(
            encoding="utf-8"
        )
        starter_text = _config_template("demo")

        assert "fact_extraction:\n    enabled:" not in example_text
        assert "emergence:\n    enabled:" not in example_text
        assert "fact_extraction:\n    enabled:" not in deploy_text
        assert "emergence:\n    enabled:" not in deploy_text
        assert "fact_extraction:\n    enabled:" not in starter_text
        assert "emergence:\n    enabled:" not in starter_text
