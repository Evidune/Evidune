"""Tests for agent/skill_synthesizer.py."""

from pathlib import Path

import pytest

from agent.pattern_detector import DetectedPattern
from agent.skill_synthesizer import SkillSynthesizer, _strip_code_fence
from skills.loader import parse_skill
from tests.conftest import MockJudge

SAMPLE_SKILL_MD = """---
name: explain-topic
description: Explain a topic clearly
tags: [teaching, explanation]
triggers:
  - user asks "explain X"
anti_triggers:
  - user wants code
---

## Instructions

Explain step by step.

## Examples

### Example 1: Explain DNS

User: explain DNS
Output: ...

## Reference Data

(auto-updated)
"""

SAMPLE_SKILL_PACKAGE = f"""<<<FILE: SKILL.md>>>
{SAMPLE_SKILL_MD}
<<<FILE: references/checklist.md>>>
# Checklist

- Confirm the user wants an explanation.
- Explain step by step.

<<<FILE: references/source-notes.md>>>
# Source Notes

Use the user's topic and prior context.
"""


class TestStripCodeFence:
    def test_no_fence(self):
        assert _strip_code_fence("hello").strip() == "hello"

    def test_markdown_fence(self):
        raw = "```markdown\nhello\n```"
        assert _strip_code_fence(raw).strip() == "hello"

    def test_no_lang_fence(self):
        raw = "```\nhello\n```"
        assert _strip_code_fence(raw).strip() == "hello"


class TestSynthesizer:
    @pytest.mark.asyncio
    async def test_writes_skill_md(self, tmp_path: Path):
        judge = MockJudge(SAMPLE_SKILL_PACKAGE)
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True,
            suggested_name="explain-topic",
            description="Explain",
            confidence=0.9,
            rationale="useful",
        )
        history = [{"role": "user", "content": "explain DNS"}]
        result = await synth.synthesize(pattern, history)
        assert result is not None
        assert result.name == "explain-topic"
        assert result.path.exists()
        assert result.path == tmp_path / "explain-topic" / "SKILL.md"
        content = result.path.read_text(encoding="utf-8")
        assert "explain-topic" in content
        assert "## Instructions" in content
        assert (tmp_path / "explain-topic" / "references" / "checklist.md").exists()
        assert (tmp_path / "explain-topic" / "references" / "source-notes.md").exists()
        assert (tmp_path / "explain-topic" / "references" / "evaluation-contract.md").exists()
        skill = parse_skill(result.path)
        assert "checklist.md" in skill.references
        assert "source-notes.md" in skill.references
        assert "evaluation-contract.md" in skill.references
        assert skill.execution_contract is not None
        assert skill.execution_contract.criteria[0].name == "goal_completion"

    @pytest.mark.asyncio
    async def test_skips_when_pattern_not_skill(self, tmp_path: Path):
        judge = MockJudge("should not be called")
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(is_skill=False)
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is None
        assert judge.last_messages is None

    @pytest.mark.asyncio
    async def test_skips_when_no_name(self, tmp_path: Path):
        judge = MockJudge("ignored")
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(is_skill=True, suggested_name="", confidence=0.9)
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_no_write_mode(self, tmp_path: Path):
        judge = MockJudge(SAMPLE_SKILL_PACKAGE)
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True, suggested_name="x", description="d", confidence=0.9
        )
        result = await synth.synthesize(pattern, [{"role": "user", "content": "test"}], write=False)
        assert result is not None
        assert not result.path.exists()
        # But the synthesised content is in memory
        assert "## Instructions" in result.skill_md

    @pytest.mark.asyncio
    async def test_strips_code_fence_around_output(self, tmp_path: Path):
        judge = MockJudge(f"```markdown\n{SAMPLE_SKILL_PACKAGE}\n```")
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True, suggested_name="explain-topic", description="d", confidence=0.9
        )
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is not None
        # Disk content should not contain the outer fence
        content = result.path.read_text(encoding="utf-8")
        assert not content.startswith("```")

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self, tmp_path: Path):
        judge = MockJudge("")
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True, suggested_name="x", description="d", confidence=0.9
        )
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_legacy_single_file_output_gets_support_files(self, tmp_path: Path):
        judge = MockJudge(SAMPLE_SKILL_MD)
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True,
            suggested_name="explain-topic",
            description="Explain",
            confidence=0.9,
            rationale="useful",
        )
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is not None
        assert (tmp_path / "explain-topic" / "references" / "checklist.md").exists()
        assert (tmp_path / "explain-topic" / "references" / "source-notes.md").exists()
        assert (tmp_path / "explain-topic" / "references" / "evaluation-contract.md").exists()
        assert parse_skill(result.path).execution_contract is not None

    @pytest.mark.asyncio
    async def test_rejects_unsafe_bundle_path(self, tmp_path: Path):
        unsafe = f"""<<<FILE: SKILL.md>>>
{SAMPLE_SKILL_MD}
<<<FILE: ../escape.md>>>
bad
"""
        judge = MockJudge(unsafe)
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path)
        pattern = DetectedPattern(
            is_skill=True,
            suggested_name="explain-topic",
            description="Explain",
            confidence=0.9,
            rationale="useful",
        )
        result = await synth.synthesize(pattern, [{"role": "user", "content": "x"}])
        assert result is None
        assert not (tmp_path / "explain-topic").exists()

    @pytest.mark.asyncio
    async def test_update_existing_skill_uses_existing_package_path(self, tmp_path: Path):
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()
        existing_path = existing_dir / "SKILL.md"
        existing_path.write_text(
            "---\nname: explain-topic\ndescription: Old\n---\n\n## Instructions\nOld.\n",
            encoding="utf-8",
        )
        existing = parse_skill(existing_path)
        judge = MockJudge(SAMPLE_SKILL_PACKAGE)
        synth = SkillSynthesizer(judge=judge, output_dir=tmp_path / "new")
        pattern = DetectedPattern(
            is_skill=True,
            suggested_name="ignored-new-name",
            description="Explain",
            confidence=0.9,
            rationale="useful",
        )

        result = await synth.synthesize(
            pattern, [{"role": "user", "content": "x"}], existing_skill=existing
        )

        assert result is not None
        assert result.name == "explain-topic"
        assert result.path == existing_path
        assert "Update the existing skill package" in judge.last_messages[0]["content"]
