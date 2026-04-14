"""Integration tests: AgentCore triggers skill emergence every N turns."""

from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from agent.pattern_detector import DetectedPattern
from agent.skill_synthesizer import SynthesisResult
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

    async def detect(self, history, existing_skill_names=None, **kw):
        self.calls += 1
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


def _make_skill_md(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "explain-topic"
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(
        "---\nname: explain-topic\ndescription: x\noutcome_metrics: false\n---\n\n## Instructions\nDo it.\n",
        encoding="utf-8",
    )
    return p


class TestEmergenceTrigger:
    @pytest.mark.asyncio
    async def test_no_detector_means_no_emergence(self, memory):
        agent = _make_agent(memory, detector=None, synthesizer=None)
        msg = InboundMessage(text="hi", sender_id="u", channel="cli", conversation_id="c")
        for _ in range(5):
            resp = await agent.handle(msg)
        assert resp.metadata["emerged_skill"] is None

    @pytest.mark.asyncio
    async def test_low_confidence_skips(self, memory, tmp_path):
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

    @pytest.mark.asyncio
    async def test_high_confidence_creates_skill(self, memory, tmp_path):
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
        assert synth.calls == 1
        # Registered in emerged_skills table
        rec = memory.get_emerged_skill("explain-topic")
        assert rec is not None
        assert rec["status"] == "pending_review"
        # And added to live registry
        assert agent.skills.get("explain-topic") is not None

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
