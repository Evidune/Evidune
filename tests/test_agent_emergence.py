"""Integration tests: AgentCore triggers skill emergence every N turns."""

import asyncio
import json
from pathlib import Path

import pytest

from agent.core import AgentCore
from agent.llm import LLMClient
from agent.pattern_detector import DetectedPattern
from agent.skill_synthesizer import SkillSynthesizer, SynthesisResult
from core.loop import _load_active_emerged_skills
from gateway.base import InboundMessage
from memory.store import MemoryStore
from skills.loader import parse_skill
from skills.registry import SkillRegistry


class MockLLM(LLMClient):
    def __init__(self, response: str = "ok"):
        self.response = response
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
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
    inline_timeout_s: float = 5.0,
):
    return AgentCore(
        llm=MockLLM(),
        skill_registry=SkillRegistry(),
        memory=memory,
        pattern_detector=detector,
        skill_synthesizer=synthesizer,
        emergence_every_n_turns=every_n,
        emergence_min_confidence=min_conf,
        emergence_inline_timeout_s=inline_timeout_s,
    )


def _make_skill_md(tmp_path: Path, name: str = "explain-topic") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(
        f"---\nname: {name}\ndescription: x\n---\n\n## Instructions\nDo it.\n",
        encoding="utf-8",
    )
    return p


def _skill_files(name: str, instructions: str = "Do it.") -> dict[str, str]:
    return {
        "SKILL.md": (
            f"---\nname: {name}\ndescription: x\n---\n\n" f"## Instructions\n{instructions}\n"
        ),
        "scripts/checklist.md": "# Checklist\n\n- Follow the workflow.\n",
        "references/source-notes.md": "# Source Notes\n\nConversation-derived notes.\n",
    }


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
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=6)
        conversation_id = "web-mo5tvtb9"
        turns = [
            "有哪些公开的信息源可以收集新闻、财经等信息？",
            "这些 API 可以未来接入吗？",
            "建立一个新闻+行情分析 skill，要能反复复用。",
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
        assert log["trigger_reason"] == "explicit_skill_request"
        assert log["detected_name"] == "news-market-analysis"
        assert log["emerged_skill_path"].endswith("news-market-analysis/SKILL.md")

    @pytest.mark.asyncio
    async def test_explicit_skill_request_bypasses_cadence(self, memory, tmp_path, capsys):
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="collect-intel",
                description="Collect public information into an intelligence brief",
                confidence=0.9,
                rationale="The user explicitly asked to create a reusable skill.",
            )
        )

        def factory(pattern, write):
            path = _make_skill_md(tmp_path, "collect-intel")
            return SynthesisResult(
                name=pattern.suggested_name, skill_md=path.read_text(), path=path
            )

        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=6)
        resp = await agent.handle(
            InboundMessage(
                text="建立一个收集资讯的 skill",
                sender_id="u",
                channel="web",
                conversation_id="c",
            )
        )
        assert resp.metadata["emerged_skill"] == "collect-intel"
        assert detector.calls == 1
        assert synth.calls == 1
        log = _read_last_log(capsys)
        assert log["emergence_counter"] == 1
        assert log["emergence_attempted"] is True
        assert log["trigger_reason"] == "explicit_skill_request"

    @pytest.mark.asyncio
    async def test_explicit_skill_request_does_not_call_normal_chat_llm(self, memory, tmp_path):
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="collect-intel",
                description="Collect public information into a brief",
                confidence=0.9,
                rationale="The user explicitly asked to create a reusable skill.",
            )
        )

        def factory(pattern, write):
            path = _make_skill_md(tmp_path, "collect-intel")
            return SynthesisResult(
                name=pattern.suggested_name, skill_md=path.read_text(), path=path
            )

        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=6)
        resp = await agent.handle(
            InboundMessage(
                text="建立一个收集资讯的 skill",
                sender_id="u",
                channel="web",
                conversation_id="c",
            )
        )

        assert agent.llm.calls == 0
        assert "已创建并激活" in resp.text
        assert resp.metadata["skill_creation"]["status"] == "created"
        assert resp.metadata["skill_creation"]["skill_name"] == "collect-intel"

    @pytest.mark.asyncio
    async def test_existing_emerged_skill_is_updated_instead_of_duplicated(self, memory, tmp_path):
        existing_path = _make_skill_md(tmp_path, "collect-intel")
        memory.register_emerged_skill(
            name="collect-intel",
            status="active",
            path=str(existing_path),
        )
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="collect-intel",
                description="Collect public information into a brief",
                confidence=0.9,
                rationale="The user explicitly asked to improve a reusable skill.",
            )
        )

        def factory(pattern, write):
            files = _skill_files("collect-intel", instructions="Use the updated workflow.")
            return SynthesisResult(
                name=pattern.suggested_name,
                skill_md=files["SKILL.md"],
                path=existing_path,
                files=files,
            )

        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=6)
        agent.skills.register(parse_skill(existing_path), source="emerged")

        resp = await agent.handle(
            InboundMessage(
                text="更新并沉淀 collect-intel skill",
                sender_id="u",
                channel="web",
                conversation_id="c",
            )
        )

        assert resp.metadata["emerged_skill"] == "collect-intel"
        assert resp.metadata["skill_creation"]["status"] == "updated"
        assert "Use the updated workflow." in existing_path.read_text(encoding="utf-8")
        rec = memory.get_emerged_skill("collect-intel")
        assert rec is not None
        assert rec["version"] == 2
        event = memory.get_latest_skill_lifecycle_event("collect-intel", action="update")
        assert event is not None
        assert event["content_before"]

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
        assert resp.metadata["skill_creation"]["status"] == "reused"
        assert resp.metadata["skill_creation"]["skill_name"] == "explain-topic"
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
    async def test_cadence_trigger_reason_logged(self, memory, tmp_path, capsys):
        detector = MockDetector(DetectedPattern(is_skill=False, confidence=0.0))
        synth = MockSynthesizer(tmp_path, lambda p, w: None)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=2)
        msg = InboundMessage(
            text="ordinary chat",
            sender_id="u",
            channel="cli",
            conversation_id="c",
        )
        await agent.handle(msg)
        await agent.handle(msg)
        log = _read_last_log(capsys)
        assert log["emergence_counter"] == 2
        assert log["emergence_attempted"] is True
        assert log["trigger_reason"] == "cadence"
        assert log["skip_reason"] == "below_threshold"

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

    @pytest.mark.asyncio
    async def test_unsafe_synthesis_bundle_reports_failed(self, memory, tmp_path, capsys):
        detector = MockDetector(
            DetectedPattern(
                is_skill=True,
                suggested_name="unsafe-skill",
                description="Unsafe",
                confidence=0.9,
                rationale="test",
            )
        )
        unsafe_bundle = """<<<FILE: SKILL.md>>>
---
name: unsafe-skill
description: Unsafe
---

## Instructions

Do it.

<<<FILE: /tmp/escape.md>>>
bad
"""
        synth = SkillSynthesizer(judge=MockLLM(unsafe_bundle), output_dir=tmp_path)
        agent = _make_agent(memory, detector=detector, synthesizer=synth, every_n=1)
        resp = await agent.handle(
            InboundMessage(
                text="建立一个 unsafe skill",
                sender_id="u",
                channel="cli",
                conversation_id="c",
            )
        )
        assert resp.metadata["emerged_skill"] is None
        assert not (tmp_path / "unsafe-skill").exists()
        log = _read_last_log(capsys)
        assert log["skip_reason"] == "synthesis_failed"
        assert log["activation_status"] == "failed"

    @pytest.mark.asyncio
    async def test_slow_emergence_is_queued_without_blocking_response(
        self, memory, tmp_path, capsys
    ):
        class SlowDetector:
            calls = 0

            async def detect(self, history, existing_skill_names=None, **kw):
                self.calls += 1
                await asyncio.sleep(0.05)
                return DetectedPattern(
                    is_skill=True,
                    suggested_name="queued-skill",
                    description="Queued skill",
                    confidence=0.9,
                    rationale="The user explicitly asked for a skill.",
                )

        def factory(pattern, write):
            path = _make_skill_md(tmp_path, "queued-skill")
            return SynthesisResult(
                name=pattern.suggested_name, skill_md=path.read_text(), path=path
            )

        detector = SlowDetector()
        synth = MockSynthesizer(tmp_path, factory)
        agent = _make_agent(
            memory,
            detector=detector,
            synthesizer=synth,
            every_n=6,
            inline_timeout_s=0.001,
        )
        resp = await agent.handle(
            InboundMessage(
                text="建立一个会后台完成的 skill",
                sender_id="u",
                channel="cli",
                conversation_id="c",
            )
        )
        assert "后台队列" in resp.text
        assert resp.metadata["emerged_skill"] is None
        assert resp.metadata["skill_creation"]["status"] == "queued"
        queued_log = _read_last_log(capsys)
        assert queued_log["skip_reason"] == "emergence_queued"
        assert queued_log["activation_status"] == "pending"
        assert queued_log["trigger_reason"] == "explicit_skill_request"

        decisions = await agent.wait_for_background_emergence(timeout_s=1)
        assert [d.emerged_skill for d in decisions] == ["queued-skill"]
        final_log = _read_last_log(capsys)
        assert final_log["activation_status"] == "activated"
        assert final_log["emerged_skill_path"].endswith("queued-skill/SKILL.md")
        assert memory.get_emerged_skill("queued-skill") is not None

    @pytest.mark.asyncio
    async def test_cancelled_background_emergence_logs_failure(self, memory, tmp_path, capsys):
        class HangingDetector:
            async def detect(self, history, existing_skill_names=None, **kw):
                await asyncio.sleep(10)
                return DetectedPattern(
                    is_skill=True,
                    suggested_name="cancelled-skill",
                    description="Cancelled skill",
                    confidence=0.9,
                    rationale="The user explicitly asked for a skill.",
                )

        synth = MockSynthesizer(tmp_path, lambda pattern, write: None)
        agent = _make_agent(
            memory,
            detector=HangingDetector(),
            synthesizer=synth,
            every_n=6,
            inline_timeout_s=0.001,
        )
        resp = await agent.handle(
            InboundMessage(
                text="创建一个会被取消的 skill",
                sender_id="u",
                channel="cli",
                conversation_id="c",
            )
        )
        assert resp.metadata["skill_creation"]["status"] == "queued"
        _read_last_log(capsys)

        tasks = list(agent._background_emergence_tasks)
        assert tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        final_log = _read_last_log(capsys)
        assert final_log["skip_reason"] == "emergence_cancelled"
        assert final_log["activation_status"] == "failed"
        assert final_log["skill_creation_status"] == "failed"
