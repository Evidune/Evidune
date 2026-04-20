"""Integration tests: AgentCore triggers skill emergence every N turns."""

import json
from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from agent.pattern_detector import DetectedPattern
from agent.skill_synthesizer import SynthesisResult
from core.loop import _load_active_emerged_skills
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry


class MockLLM(LLMClient):
    def __init__(self, response: str = "ok"):
        self.response = response

    async def complete(self, messages, **kwargs):
        return self.response


class MockDetector:
    def __init__(self, pattern: DetectedPattern):
        self.pattern = pattern
        self.calls = 0
        self.last_history = None

    async def detect(self, history, existing_skill_names=None, **kw):
        self.calls += 1
        self.last_history = history
        return self.pattern


class MockSynthesizer:
    def __init__(self, output_dir: Path, result_factory):
        self.output_dir = output_dir
        self.result_factory = result_factory
        self.calls = 0

    async def synthesize(self, pattern, history, write=True, **kw):
        self.calls += 1
        return self.result_factory(pattern, write)


@pytest.fixture
def memory(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    yield s
    s.close()


def _make_agent(
    memory: MemoryStore,
    *,
    detector=None,
    synthesizer=None,
    every_n: int = 3,
    min_conf: float = 0.7,
):
    return AgentCore(
        llm=MockLLM(),
        skill_registry=SkillRegistry(),
        memory=memory,
        pattern_detector=detector,
        skill_synthesizer=synthesizer,
        emergence_every_n_turns=every_n,
        emergence_min_confidence=min_conf,
    )


def _make_skill_md(tmp_path: Path, name: str = "explain-topic") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(
        f"---\nname: {name}\ndescription: x\noutcome_metrics: false\n---\n\n## Instructions\nDo it.\n",
        encoding="utf-8",
    )
    return p


def _read_last_log(capsys) -> dict:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return json.loads(lines[-1])


class TestEmergenceTrigger:
    @pytest.mark.asyncio
    async def test_no_detector_means_no_emergence(self, memory, capsys):
        agent = _make_agent(memory, detector=None, synthesizer=None)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(5):
            resp = await agent.handle(msg)
        assert resp.metadata["emerged_skill"] is None
        log = _read_last_log(capsys)
        assert log["skip_reason"] == "disabled_by_config"
        assert log["emergence_attempted"] is False

    @pytest.mark.asyncio
    async def test_low_confidence_skips(self, memory, tmp_path, capsys):
        detector = MockDetector(
            DetectedPattern(is_skill=True, suggested_name="x", description="d", confidence=0.3)
        )
        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=1)
        msg = InboundMessage(text="t", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        assert resp.metadata["emerged_skill"] is None
        # Detector was called, but synth was not (confidence too low)
        assert detector.calls == 1
        assert synth.calls == 0
        log = _read_last_log(capsys)
        assert log["skip_reason"] == "below_threshold"
        assert log["detected_name"] == "x"
        assert log["detected_confidence"] == 0.3

    @pytest.mark.asyncio
    async def test_skill_design_conversation_creates_active_skill(self, memory, tmp_path, capsys):
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="news-market-analysis",
                description="Analyse news and market context with a reusable workflow",
                confidence=0.9,
                rationale="The conversation is designing a reusable skill.",
            )
        )

        def factory(pattern, write):
            path = _make_skill_md(tmp_path, "news-market-analysis")
            return SynthesisResult(
                name=pattern.suggested_name, skill_md=path.read_text(), path=path
            )

        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=3)
        conversation_id = "web-mo5tvtb9"
        turns = [
            "可以自己搜索网页吗？",
            "是需要你自己接入，形成 skill。",
            "你自己实现一个新闻+行情分析 skill，要能反复复用。",
        ]
        for text in turns[:-1]:
            await agent.handle(
                InboundMessage(
                    text=text, sender_id="u", channel="web", conversation_id=conversation_id
                )
            )
        resp = await agent.handle(
            InboundMessage(
                text=turns[-1], sender_id="u", channel="web", conversation_id=conversation_id
            )
        )
        assert resp.metadata["emerged_skill"] == "news-market-analysis"
        assert synth.calls == 1
        assert detector.calls == 1
        assert detector.last_history is not None
        assert any("新闻+行情分析 skill" in item["content"] for item in detector.last_history)
        rec = memory.get_emerged_skill("news-market-analysis")
        assert rec is not None
        assert rec["status"] == "active"
        state = memory.get_skill_state("news-market-analysis")
        assert state is not None
        assert state["status"] == "active"
        assert state["origin"] == "emerged"
        assert rec["path"].endswith("news-market-analysis/SKILL.md")
        event = memory.get_latest_skill_lifecycle_event("news-market-analysis", action="activate")
        assert event is not None
        assert event["status"] == "active"
        assert agent.skills.get("news-market-analysis") is not None
        log = _read_last_log(capsys)
        assert log["conversation_id"] == conversation_id
        assert log["emergence_counter"] == 3
        assert log["emergence_attempted"] is True
        assert log["activation_status"] == "activated"
        assert log["skip_reason"] == ""
        assert log["detected_name"] == "news-market-analysis"
        assert log["emerged_skill_path"].endswith("news-market-analysis/SKILL.md")

    @pytest.mark.asyncio
    async def test_duplicate_name_skipped(self, memory, tmp_path):
        # Pre-load a skill with the same name
        existing_dir = tmp_path / "explain-topic"
        existing_dir.mkdir(parents=True)
        (existing_dir / "SKILL.md").write_text(
            "---\nname: explain-topic\ndescription: existing\n---\n\n## Instructions\nx",
            encoding="utf-8",
        )

        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="explain-topic",
                description="d",
                confidence=0.9,
            )
        )
        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=1)
        # Pre-register
        from skills.loader import parse_skill

        agent.skills.register(parse_skill(existing_dir / "SKILL.md"))

        msg = InboundMessage(text="x", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        assert resp.metadata["emerged_skill"] is None
        # Synthesiser should NOT have been called
        assert synth.calls == 0

    @pytest.mark.asyncio
    async def test_every_n_turns_gating(self, memory, tmp_path):
        detector = MockDetector(DetectedPattern(is_skill=False))
        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=3)
        msg = InboundMessage(text="t", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(7):
            await agent.handle(msg)
        # Triggered at turns 3 and 6 = 2 detector calls
        assert detector.calls == 2

    @pytest.mark.asyncio
    async def test_turn_count_survives_agent_restart(self, memory, tmp_path):
        detector = MockDetector(DetectedPattern(is_skill=False, confidence=0.0))
        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        first = _make_agent(memory, detector=detector, synthesizer=synth, every_n=2)
        second = _make_agent(memory, detector=detector, synthesizer=synth, every_n=2)
        msg = InboundMessage(text="t", sender_id="u", channel="cli", conversation_id="c")
        await first.handle(msg)
        await second.handle(msg)
        assert memory.get_conversation("c")["turn_count"] == 2
        assert detector.calls == 1

    @pytest.mark.asyncio
    async def test_emerged_skill_reloads_from_persisted_metadata(self, memory, tmp_path):
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="explain-topic",
                description="Explain",
                confidence=0.9,
                rationale="ok",
            )
        )

        def factory(pattern, write):
            path = _make_skill_md(tmp_path)
            return SynthesisResult(
                name=pattern.suggested_name, skill_md=path.read_text(), path=path
            )

        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=1)
        msg = InboundMessage(text="explain DNS", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        assert resp.metadata["emerged_skill"] == "explain-topic"

        reloaded = SkillRegistry()
        loaded = _load_active_emerged_skills(reloaded, memory, tmp_path)
        assert loaded == 1
        assert reloaded.get("explain-topic") is not None

    @pytest.mark.asyncio
    async def test_detector_failure_swallowed(self, memory, tmp_path):
        class BoomDetector:
            async def detect(self, *a, **kw):
                raise RuntimeError("crash")

        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        agent = _make_agent(memory, detector=BoomDetector(), synthesizer=synth, every_n=1)
        msg = InboundMessage(text="x", sender_id="u", channel="cli", conversation_id="c")
        resp = await agent.handle(msg)
        # Response delivered normally; emerged_skill is None
        assert resp.text == "ok"
        assert resp.metadata["emerged_skill"] is None
